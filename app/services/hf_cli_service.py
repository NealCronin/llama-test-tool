"""Process manager around the installed official `hf` CLI.

Downloads are executed by the real `hf download` binary through QProcess (never a
shell), one item at a time. `hf` has no dry-run or file-listing subcommand in the
installed release, so selection is expressed as explicit filenames and/or
`--include`/`--exclude` globs, the exact argv is previewed before start, and
downloaded files are detected by diffing the target folder before and after the
run — a glob that matches nothing exits 0 silently, and that case is reported.
"""
from __future__ import annotations

import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from app.models.hf_download import HfDownloadRequest, HfDownloadResult


class HfCliError(Exception):
    pass


@dataclass(frozen=True)
class HfCliInfo:
    path: str
    hub_version: str


def find_hf_cli() -> HfCliInfo:
    """Locate the official `hf` CLI on PATH and read the huggingface_hub version."""
    exe = shutil.which("hf") or shutil.which("hf.exe")
    if not exe:
        raise HfCliError(
            "The official 'hf' CLI was not found on PATH. Install it with: python -m pip install -U huggingface_hub"
        )
    try:
        proc = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise HfCliError(f"Could not run 'hf version': {error}") from error
    version = "unknown"
    for line in (proc.stdout or "").splitlines():
        if "huggingface_hub version:" in line:
            version = line.split(":", 1)[1].strip() or "unknown"
    return HfCliInfo(path=exe, hub_version=version)


def build_download_argv(hf_path: str, request: HfDownloadRequest, local_dir: Path) -> list[str]:
    argv = [hf_path, "download", request.repo_id]
    argv.extend(request.filenames)
    if request.revision:
        argv += ["--revision", request.revision]
    if request.include:
        argv += ["--include", *request.include]
    if request.exclude:
        argv += ["--exclude", *request.exclude]
    argv += ["--local-dir", str(local_dir)]
    if request.max_workers:
        argv += ["--max-workers", str(request.max_workers)]
    if request.token:
        argv += ["--token", request.token]
    argv.append("--quiet")
    return argv


def render_command_line(argv: list[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) for part in argv)


def _snapshot(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file()}


def _tail(lines: list[str], count: int = 12) -> str:
    return "\n".join(lines[-count:])


class HfCliService(QObject):
    """Sequential download queue executing `hf download` via QProcess."""

    output = Signal(str, str)  # request id, console line
    state_changed = Signal(str, str)  # request id, state text
    finished = Signal(str, object)  # request id, HfDownloadResult
    all_finished = Signal(bool)  # True when the queue is fully drained (any successful download included)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: deque[tuple[str, HfDownloadRequest, Path]] = deque()
        self._process: QProcess | None = None
        self._current: tuple[str, HfDownloadRequest, Path, set[str]] | None = None
        self._stdout_tail: list[str] = []
        self._stderr_tail: list[str] = []
        self._stdout_pending = ""
        self._stderr_pending = ""
        self._counter = 0
        self._had_success = False
        self._info: HfCliInfo | None = None
        self._stopping = False

    @property
    def busy(self) -> bool:
        return bool(self._process is not None or self._queue)

    def enqueue(self, request: HfDownloadRequest, local_dir: Path) -> str:
        self._counter += 1
        request_id = f"hf-{self._counter}"
        self._queue.append((request_id, request, local_dir))
        self.state_changed.emit(request_id, "Queued")
        self.output.emit(request_id, f"Queued: {request.describe()}")
        self._pump()
        return request_id

    def stop(self) -> None:
        """Cancel queued items and terminate the running download."""
        self._stopping = True
        while self._queue:
            request_id, request, _ = self._queue.popleft()
            result = HfDownloadResult(request=request, success=False, detail="Cancelled before start.")
            self._record(request_id, result)
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self.output.emit(self._current[0] if self._current else "", "Stopping: terminating the hf process…")
            self._process.terminate()
            if self._process.state() != QProcess.ProcessState.NotRunning:
                self._process.kill()

    def _pump(self) -> None:
        if self._process is not None or not self._queue:
            self._drain_check()
            return
        request_id, request, local_dir = self._queue.popleft()
        if self._info is None:
            try:
                self._info = find_hf_cli()
            except HfCliError as error:
                self._record(request_id, HfDownloadResult(request=request, success=False, detail=str(error)))
                self._pump()
                return
        info = self._info
        local_dir.mkdir(parents=True, exist_ok=True)
        argv = build_download_argv(info.path, request, local_dir)
        self.state_changed.emit(request_id, "Downloading")
        self.output.emit(request_id, f"[hf {info.hub_version}] {render_command_line(argv)}")
        before = _snapshot(local_dir)
        process = QProcess(self)
        self._process = process
        self._current = (request_id, request, local_dir, before)
        self._stdout_tail, self._stderr_tail = [], []
        self._stdout_pending, self._stderr_pending = "", ""
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._on_finished)
        process.start(info.path, argv[1:])

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardOutput().data()).decode("utf-8", "replace")
        self._stdout_pending += chunk
        while "\n" in self._stdout_pending:
            line, self._stdout_pending = self._stdout_pending.split("\n", 1)
            self._emit_line(line, "stdout")

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        chunk = bytes(self._process.readAllStandardError().data()).decode("utf-8", "replace")
        self._stderr_pending += chunk
        while "\n" in self._stderr_pending:
            line, self._stderr_pending = self._stderr_pending.split("\n", 1)
            self._emit_line(line, "stderr")

    def _emit_line(self, line: str, stream: str) -> None:
        if not line.strip():
            return
        request_id = self._current[0] if self._current else ""
        self.output.emit(request_id, line)
        tail = self._stdout_tail if stream == "stdout" else self._stderr_tail
        tail.append(line)
        if len(tail) > 12:
            tail.pop(0)

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.deleteLater()
        if self._current is None:
            return
        request_id, request, local_dir, before = self._current
        self._current = None
        # Flush buffered tails.
        for pending, handler in ((self._stdout_pending, self._stdout_tail), (self._stderr_pending, self._stderr_tail)):
            if pending.strip():
                handler.append(pending)
        self._stdout_pending = self._stderr_pending = ""
        after = _snapshot(local_dir)
        new_files = sorted(after - before)
        if self._stopping:
            self._stopping = False
            result = HfDownloadResult(request=request, success=False, files=new_files, detail="Stopped by user.")
        elif status == QProcess.ExitStatus.NormalExit and code == 0:
            detail = f"{len(new_files)} new file(s) in {local_dir.name}." if new_files else "Completed, but no new files were detected — the selection may match nothing."
            result = HfDownloadResult(request=request, success=True, files=new_files, detail=detail)
        else:
            error_lines = self._stderr_tail or self._stdout_tail or [f"hf exited with code {code}."]
            result = HfDownloadResult(request=request, success=False, files=new_files, detail=_tail(error_lines))
        self._record(request_id, result)
        self._pump()

    def _record(self, request_id: str, result: HfDownloadResult) -> None:
        self._had_success = self._had_success or result.success
        state = "Done" if result.success else "Failed" if not result.detail.startswith(("Cancelled", "Stopped")) else "Cancelled"
        self.state_changed.emit(request_id, state)
        self.output.emit(request_id, f"{state}: {result.request.describe()} — {result.detail}")
        self.finished.emit(request_id, result)

    def _drain_check(self) -> None:
        if self._process is None and not self._queue:
            self.all_finished.emit(self._had_success)
            self._had_success = False

