"""Asynchronous process manager around the installed official ``hf`` CLI.

Every CLI interaction — version, auth, ``download --help`` capability probing,
dry-run previews, and downloads — runs through QProcess so a slow or hanging
``hf`` can never block the GUI thread. ``shutil.which`` is the only synchronous
discovery step.

Secrets: the service never accepts or renders tokens. Authentication is the
``hf`` CLI's own stored credential (``hf auth login``) or an inherited
``HF_TOKEN`` environment variable, and :func:`redact_secrets` is applied to
anything that is ever shown, as defense in depth.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from app.models.hf_download import (
    HfAuthStatus,
    HfCliCapabilities,
    HfCliInfo,
    HfDownloadRequest,
    HfDownloadResult,
    HfDryRunFile,
    HfDryRunReport,
    HfJobState,
    HfRepoType,
    HfSelectionMode,
)

# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without Qt event loops)
# ---------------------------------------------------------------------------


class HfCliError(Exception):
    """Raised when the hf CLI cannot satisfy a request."""


def locate_hf_cli() -> str | None:
    """Synchronous, process-free discovery. Safe to call on the GUI thread."""
    return shutil.which("hf") or shutil.which("hf.exe")


def render_command_line(argv: list[str]) -> str:
    """Render an argv list for display. Execution always uses the argv list."""
    return " ".join(subprocess.list2cmdline([part]) for part in argv)


_SECRET = re.compile(r"(?<![A-Za-z0-9_])(hf_[A-Za-z0-9]{8,})")


def redact_secrets(text: str) -> str:
    """Mask anything that looks like a Hugging Face token (defense in depth)."""
    return _SECRET.sub("hf_***", text)


def parse_hub_version(text: str) -> str:
    """``hf version`` prints ``huggingface_hub version: X.Y.Z``."""
    for line in text.splitlines():
        if "huggingface_hub version:" in line:
            return line.split(":", 1)[1].strip()
    return ""


def parse_whoami(text: str) -> str:
    """Best-effort username extraction from ``hf auth whoami`` output.

    Historical formats include ``user:  <username>`` and a bare username line
    (possibly followed by ``orgs:``). The empty string means "no username
    recognized" — callers must treat the CLI exit status as the primary
    authentication signal, never this text alone.
    """
    for line in text.splitlines():
        match = re.match(r"\s*user\s*:\s*(\S+)", line, re.IGNORECASE)
        if match:
            return match.group(1)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or ":" in stripped or " " in stripped:
            continue  # header-ish, blank, or sentence text (e.g. an error line)
        return stripped
    return ""


_HELP_FLAGS = {
    "repo_type": "--repo-type",
    "revision": "--revision",
    "include": "--include",
    "exclude": "--exclude",
    "cache_dir": "--cache-dir",
    "local_dir": "--local-dir",
    "force_download": "--force-download",
    "dry_run": "--dry-run",
    "max_workers": "--max-workers",
}


def parse_download_help(text: str) -> dict[str, bool]:
    """Record which optional ``hf download`` flags the installed CLI offers."""
    return {name: flag in text for name, flag in _HELP_FLAGS.items()}


_DRY_RUN_SUMMARY = re.compile(
    r"\[dry-run\]\s+Will download\s+(\d+)\s+files?\s+\(out of\s+(\d+)\)\s+totalling\s+(.+?)\.?\s*$",
    re.MULTILINE,
)


def parse_dry_run(text: str, exit_code: int = 0) -> HfDryRunReport:
    """Parse ``hf download --dry-run`` output (huggingface_hub >= 1.0.0).

    The 1.x CLI prints a summary line plus a two-column table. The summary is
    the source of truth for the counts; the table is decoded conservatively
    from the declared size column (1.x ``FILE``/``SIZE`` or the older
    ``File``/``Bytes to download`` header). When the shape is unrecognizable
    the report keeps ``parsed=False`` so the caller shows the raw text instead
    of inventing rows.
    """
    match = _DRY_RUN_SUMMARY.search(text)
    if match is None:
        return HfDryRunReport(exit_code=exit_code, parsed=False, raw=text)
    return HfDryRunReport(
        exit_code=exit_code,
        total_files=int(match.group(2)),
        transfer_files=int(match.group(1)),
        transfer_text=match.group(3).strip(),
        files=tuple(_parse_dry_run_table(text)),
        parsed=True,
        raw=text,
    )


_DRY_RUN_SIZE_HEADER = re.compile(r"^\s*(?:\S.*?\s+)?(SIZE|BYTES TO DOWNLOAD)\s*$", re.IGNORECASE)


def _parse_dry_run_table(text: str) -> list[HfDryRunFile]:
    """Extract (filename, size) rows from the human dry-run table.

    The size column is located from the header token, never assumed at a
    fixed offset. Returns an empty list when no recognizable header exists;
    the caller keeps the summary counts in that case.
    """
    lines = text.splitlines()
    header_index = -1
    size_col = -1
    for index, line in enumerate(lines):
        marker = "Bytes to download" if "Bytes to download" in line else None
        if marker is None:
            match = _DRY_RUN_SIZE_HEADER.match(line)
            if match:
                marker, size_col = match.group(1), match.start(1)
        else:
            size_col = line.index("Bytes to download")
        if marker is not None:
            header_index = index
            break
    if header_index < 0:
        return []
    files: list[HfDryRunFile] = []
    for line in lines[header_index + 1 :]:
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-", " "}:
            continue  # blank or dash separator row
        if size_col < len(line):
            filename = line[:size_col].strip()
            size = line[size_col:].strip()
        else:
            filename, size = stripped, ""
        if not filename:
            continue
        files.append(HfDryRunFile(filename=filename, size_text="" if size == "-" else size, will_download=size != "-"))
    return files


def build_download_argv(caps: HfCliCapabilities, request: HfDownloadRequest, dry_run: bool = False) -> list[str]:
    """Render the exact argv for ``hf download``; raises HfCliError when the
    installed CLI lacks an option the request needs. Never a shell string."""

    def require(capability: str) -> None:
        if not getattr(caps, capability):
            raise HfCliError(
                f"The installed hf CLI does not support {_HELP_FLAGS[capability]}; update with: python -m pip install -U huggingface_hub"
            )

    if request.repo_type is not HfRepoType.MODEL:
        require("repo_type")
    if request.revision:
        require("revision")
    if request.include:
        require("include")
    if request.exclude:
        require("exclude")
    if request.local_dir:
        require("local_dir")
    if request.cache_dir:
        require("cache_dir")
    if request.force_download:
        require("force_download")
    if request.max_workers is not None:
        require("max_workers")
    if dry_run:
        require("dry_run")

    argv = [caps.path, "download", request.repo_id]
    if request.selection_mode is HfSelectionMode.EXACT:
        argv.extend(request.filenames)
    if request.repo_type is not HfRepoType.MODEL:
        argv += ["--repo-type", request.repo_type.value]
    if request.revision:
        argv += ["--revision", request.revision]
    for pattern in request.include:
        argv += ["--include", pattern]
    for pattern in request.exclude:
        argv += ["--exclude", pattern]
    if request.cache_dir:
        argv += ["--cache-dir", request.cache_dir]
    if request.local_dir:
        argv += ["--local-dir", request.local_dir]
    if request.force_download:
        argv.append("--force-download")
    if request.max_workers is not None:
        argv += ["--max-workers", str(request.max_workers)]
    if dry_run:
        # Dry-run needs the normal hf output because the preview parser
        # consumes the summary/table. Do not suppress it with quiet output
        # mode.
        argv.append("--dry-run")
    return argv
def _snapshot(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}



# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class HfJob:
    id: str
    request: HfDownloadRequest
    state: HfJobState
    result: HfDownloadResult | None = None


class HfCliService(QObject):
    """Sequential download queue plus asynchronous CLI probes.

    Exactly one download process runs at a time; further jobs stay queued and
    can be reordered, removed, or retried from the UI while one is active.
    Cancellation kills only the active process — the queue survives.
    """

    info_ready = Signal(object)  # HfCliInfo
    capabilities_ready = Signal(object)  # HfCliCapabilities
    auth_ready = Signal(object)  # HfAuthStatus
    job_output = Signal(str, str)  # job id, console line
    job_state_changed = Signal(str, str)  # job id, HfJobState
    job_finished = Signal(str, object)  # job id, HfDownloadResult
    preview_finished = Signal(object)  # HfDryRunReport
    queue_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.info = HfCliInfo(path="")
        self.capabilities: HfCliCapabilities | None = None
        self.auth = HfAuthStatus()
        self._jobs: dict[str, HfJob] = {}
        self._order: list[str] = []
        self._queue: deque[str] = deque()
        self._active: str | None = None
        self._process: QProcess | None = None
        self._preview_process: QProcess | None = None
        self._preview_generation = 0
        self._cancelling = False
        self._before: set[str] = set()
        self._counter = 0
        self._output_cap = 2000

    # ------------------------------------------------------------------ probes
    def probe(self) -> None:
        """Refresh CLI path, version, capabilities, and auth — all async.

        Each probe is its own QProcess; none blocks the GUI thread, and a
        hanging ``hf`` only leaves the corresponding status field stale.
        """
        path = locate_hf_cli()
        if not path:
            self.info = HfCliInfo(path="")
            self.capabilities = HfCliCapabilities(path="")
            self.auth = HfAuthStatus()
            self.info_ready.emit(self.info)
            self.capabilities_ready.emit(self.capabilities)
            self.auth_ready.emit(self.auth)
            return
        self.info = HfCliInfo(path=path, hub_version=self.info.hub_version)
        self.info_ready.emit(self.info)
        self._spawn([path, "version"], on_done=lambda output, code, _status: self._on_version(output, code))
        self._spawn([path, "download", "--help"], on_done=lambda output, code, _status: self._on_help(path, output, code))
        self._spawn([path, "auth", "whoami"], on_done=lambda output, code, _status: self._on_whoami(output, code))

    def _on_version(self, output: str, code: int) -> None:
        version = parse_hub_version(output) if code == 0 else ""
        self.info = HfCliInfo(path=self.info.path, hub_version=version)
        self.info_ready.emit(self.info)

    def _on_help(self, path: str, output: str, code: int) -> None:
        flags = parse_download_help(output) if code == 0 else dict.fromkeys(_HELP_FLAGS, False)
        self.capabilities = HfCliCapabilities(path=path, hub_version=self.info.hub_version, **flags)
        self.capabilities_ready.emit(self.capabilities)

    def _on_whoami(self, output: str, code: int) -> None:
        # Exit status is the authentication truth; the username is best-effort
        # decoration so a changed textual format never logs out a user.
        username = parse_whoami(output) if code == 0 else ""
        self.auth = HfAuthStatus(authenticated=code == 0, username=username)
        self.auth_ready.emit(self.auth)

    # ------------------------------------------------------------------ queue
    def jobs(self) -> list[HfJob]:
        """Rows in user-visible order: active first, then the pending queue in
        exact execution order, then finished history in submission order.

        ``move_queued`` reorders the pending deque; because the deque is
        reflected here, the visible table and the actual execution sequence
        can never diverge.
        """
        seen: set[str] = set()
        rows: list[HfJob] = []
        current = self._active
        if current is not None and current in self._jobs:
            rows.append(self._jobs[current])
            seen.add(current)
        for job_id in self._queue:
            if job_id in self._jobs and job_id not in seen:
                rows.append(self._jobs[job_id])
                seen.add(job_id)
        for job_id in self._order:
            if job_id in self._jobs and job_id not in seen:
                rows.append(self._jobs[job_id])
                seen.add(job_id)
        return rows

    def job(self, job_id: str) -> HfJob | None:
        return self._jobs.get(job_id)

    @property
    def busy(self) -> bool:
        """True while a download process is running."""
        return self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning

    @property
    def active_id(self) -> str | None:
        return self._active

    def request_download(self, request: HfDownloadRequest) -> str:
        """Enqueue an immutable request snapshot; the form may change freely afterwards."""
        self._counter += 1
        job_id = f"hf-{self._counter}"
        self._jobs[job_id] = HfJob(job_id, request, HfJobState.QUEUED)
        self._order.append(job_id)
        self._queue.append(job_id)
        self.job_state_changed.emit(job_id, HfJobState.QUEUED)
        self.queue_changed.emit()
        self._pump()
        return job_id

    def remove_queued(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state is not HfJobState.QUEUED or job_id not in self._queue:
            return False
        self._queue.remove(job_id)
        del self._jobs[job_id]
        self.queue_changed.emit()
        return True

    def move_queued(self, job_id: str, delta: int) -> bool:
        """Reorder a queued (never the active) job within the pending queue."""
        items = list(self._queue)
        if job_id not in items:
            return False
        index = items.index(job_id)
        target = index + delta
        if target < 0 or target >= len(items):
            return False
        items[index], items[target] = items[target], items[index]
        self._queue.clear()
        self._queue.extend(items)
        self.queue_changed.emit()
        return True

    def retry(self, job_id: str) -> bool:
        """Re-queue a failed or cancelled job (its original request is reused)."""
        job = self._jobs.get(job_id)
        if job is None or job.state not in (HfJobState.FAILED, HfJobState.CANCELLED):
            return False
        job.state = HfJobState.QUEUED
        job.result = None
        self._queue.append(job_id)
        self.job_state_changed.emit(job_id, HfJobState.QUEUED)
        self.queue_changed.emit()
        self._pump()
        return True

    def clear_queue(self) -> None:
        """Remove every not-yet-started job; the active job is untouched."""
        if not self._queue:
            return
        for job_id in list(self._queue):
            self._queue.remove(job_id)
            del self._jobs[job_id]
        self.queue_changed.emit()

    def cancel_active(self) -> None:
        """Terminate the running download; the remaining queue stays queued.

        Graceful first: terminate(), then a delayed kill if the process has
        not exited within five seconds.
        """
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._cancelling = True
        process.terminate()
        self._arm_kill_fallback(process)

    @staticmethod
    def _arm_kill_fallback(process: QProcess) -> None:
        def kill_if_alive() -> None:
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
            except RuntimeError:
                pass  # process object already destroyed

        QTimer.singleShot(5_000, kill_if_alive)

    def shutdown(self) -> None:
        """App-exit path: stop the active process and drop the pending queue.

        Kills immediately (no graceful window): a QProcess destroyed while
        still running can abort the process on Windows.
        """
        self.clear_queue()
        self._preview_generation += 1  # discard any in-flight preview callbacks
        for process in (self._process, self._preview_process):
            if process is None:
                continue
            try:
                running = process.state() != QProcess.ProcessState.NotRunning
            except RuntimeError:
                continue  # C++ object already deleted
            if not running:
                continue
            self._cancelling = True
            try:
                process.kill()
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ preview
    @property
    def dry_run_supported(self) -> bool:
        return bool(self.capabilities is not None and self.capabilities.path and self.capabilities.dry_run)

    def preview(self, request: HfDownloadRequest) -> None:
        """Run a real ``hf download ... --dry-run`` (non-blocking).

        The dry run never mutates the destination. Raises HfCliError when the
        installed CLI predates ``--dry-run`` (huggingface_hub < 1.0.0).

        Each call starts a new generation; only the newest generation may
        publish its result, so a stale preview finishing late can never
        overwrite a newer one. The Python reference to a finished process is
        dropped as soon as its result is published, never kept past the C++
        deleteLater.
        """
        caps = self.capabilities
        argv = build_download_argv(caps, request, dry_run=True)

        self._preview_generation += 1
        generation = self._preview_generation

        old = self._preview_process
        if old is not None:
            try:
                if old.state() != QProcess.ProcessState.NotRunning:
                    # Terminate the previous preview; its late callbacks are
                    # discarded by the generation guard below.
                    old.kill()
            except RuntimeError:
                pass  # C++ object already deleted; nothing left to stop
            self._preview_process = None

        def on_done(output: str, code: int, _status) -> None:
            if generation != self._preview_generation:
                return  # stale preview: a newer one has taken over
            self._preview_process = None
            self.preview_finished.emit(parse_dry_run(output, code))

        self._preview_process = self._spawn(argv, on_done=on_done)

    # ------------------------------------------------------------------ pump
    def _pump(self) -> None:
        if self._process is not None or not self._queue:
            return
        job_id = self._queue.popleft()
        job = self._jobs[job_id]
        caps = self.capabilities
        if caps is None or not caps.path:
            self._finish_job(job, HfDownloadResult(request=job.request, exit_code=-1, ok=False, detail="hf CLI not found on PATH. Install with: python -m pip install -U huggingface_hub"))
            self._pump()
            return
        if job.request.local_dir:
            Path(job.request.local_dir).mkdir(parents=True, exist_ok=True)
        try:
            argv = build_download_argv(caps, job.request)
        except HfCliError as error:
            self._finish_job(job, HfDownloadResult(request=job.request, exit_code=-1, ok=False, detail=str(error)))
            self._pump()
            return
        job.state = HfJobState.DOWNLOADING
        self.job_state_changed.emit(job_id, job.state)
        self.job_output.emit(job_id, redact_secrets(f"$ {render_command_line(argv)}"))
        self._before = _snapshot(Path(job.request.local_dir)) if job.request.local_dir else set()
        self._active = job_id
        self._cancelling = False
        self._process = self._spawn(
            argv,
            on_line=lambda line: self.job_output.emit(job_id, redact_secrets(line)),
            on_done=lambda output, code, status: self._on_download_finished(job, output, code, status),
        )

    def _on_download_finished(self, job: HfJob, output: str, code: int, status) -> None:
        self._process = None
        self._active = None
        after = _snapshot(Path(job.request.local_dir)) if job.request.local_dir else set()
        new_files = tuple(sorted(after - self._before))
        if self._cancelling:
            state, ok, detail = HfJobState.CANCELLED, False, "Cancelled by user."
        elif code == 0 and status == QProcess.ExitStatus.NormalExit:
            # Exit status is the primary success criterion; the folder diff is
            # supplemental, so cached or force-replaced files are not failures.
            state, ok = HfJobState.COMPLETED, True
            detail = f"Completed successfully; {len(new_files)} new file(s)." if new_files else "Completed successfully; no new pathnames were created."
        else:
            state, ok = HfJobState.FAILED, False
            tail = [line for line in output.splitlines() if line.strip()][-5:]
            detail = "\n".join(tail) if tail else f"hf exited with code {code}."
        result = HfDownloadResult(request=job.request, exit_code=code, ok=ok, detail=redact_secrets(detail), new_files=new_files, output=redact_secrets(output))
        self._finish_job(job, result, state=state)
        self._pump()

    def _finish_job(self, job: HfJob, result: HfDownloadResult, state: HfJobState | None = None) -> None:
        job.state = state or (HfJobState.COMPLETED if result.ok else HfJobState.FAILED)
        job.result = result
        self.job_state_changed.emit(job.id, job.state)
        self.job_output.emit(job.id, f"{job.state.value}: {result.detail}")
        self.job_finished.emit(job.id, result)
        self.queue_changed.emit()

    # ------------------------------------------------------------------ process
    def _spawn(self, argv: list[str], on_line=None, on_done=None) -> QProcess:
        """Start a QProcess; report line chunks and the final merged output.

        Handles start failures (errorOccurred without finished) so probes and
        jobs always reach a terminal state.
        """
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        state = {"pending": "", "lines": [], "done": False}

        def emit_line(line: str) -> None:
            if not line.strip():
                return
            state["lines"].append(line)
            if len(state["lines"]) > self._output_cap:
                del state["lines"][: len(state["lines"]) - self._output_cap]
            if on_line:
                on_line(line)

        def finish(code: int, status, output_override: str | None = None) -> None:
            if state["done"]:
                return
            state["done"] = True
            if state["pending"].strip():
                emit_line(state["pending"])
            if on_done:
                on_done(output_override if output_override is not None else "\n".join(state["lines"]), code, status)
            for signal in (process.readyReadStandardOutput, process.errorOccurred, process.finished):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            try:
                process.deleteLater()
            except RuntimeError:
                pass

        def drain() -> None:
            try:
                data = bytes(process.readAllStandardOutput().data()).decode("utf-8", "replace")
            except RuntimeError:
                return
            text = state["pending"] + data
            state["pending"] = ""
            while "\n" in text:
                line, text = text.split("\n", 1)
                emit_line(line)
            state["pending"] = text

        def on_error(_error) -> None:
            finish(-1, QProcess.ExitStatus.CrashExit, "Failed to start the hf process.")

        def on_finished(code: int, status) -> None:
            drain()
            finish(code, status)

        process.readyReadStandardOutput.connect(drain)
        process.errorOccurred.connect(on_error)
        process.finished.connect(on_finished)
        process.start(argv[0], argv[1:])
        return process
