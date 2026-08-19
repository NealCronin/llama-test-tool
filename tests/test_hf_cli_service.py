from __future__ import annotations

import os
import sys
import tempfile
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QProcess

QApplication.instance() or QApplication([])
from app.models.hf_download import (
    HfCliCapabilities,
    HfDownloadRequest,
    HfDownloadResult,
    HfJobState,
    HfRepoType,
    HfSelectionMode,
    HfTarget,
)
from app.services.hf_cli_service import (
    HfCliError,
    HfCliService,
    build_download_argv,
    locate_hf_cli,
    parse_download_help,
    parse_dry_run,
    parse_hub_version,
    parse_whoami,
    redact_secrets,
    render_command_line,
    _snapshot,
)

SENTINEL = "hf_SUPERSECRET123456"

# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


def test_request_normalization_strips_and_drops_blank_entries():
    request = HfDownloadRequest(
        repo_id="  owner/repo ",
        selection_mode=HfSelectionMode.EXACT,
        filenames=[" a.gguf ", "", "  ", "b.gguf"],
        revision=" main ",
        local_dir=" C:\\models ",
    )
    assert request.repo_id == "owner/repo"
    assert request.filenames == ("a.gguf", "b.gguf")
    assert request.revision == "main"
    assert request.local_dir == "C:\\models"


def test_request_entire_repository_needs_no_selection():
    request = HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.ENTIRE)
    assert request.filenames == ()
    assert request.selection_summary() == "entire repository"


def test_request_entire_repository_rejects_patterns():
    with pytest.raises(ValueError, match="Entire-repository"):
        HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.ENTIRE, include=["*.gguf"])


def test_request_exact_requires_filenames():
    with pytest.raises(ValueError, match="at least one file name"):
        HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.EXACT)


def test_request_patterns_requires_include():
    with pytest.raises(ValueError, match="at least one include pattern"):
        HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.PATTERNS, exclude=["*.bin"])


def test_request_rejects_local_dir_and_cache_dir_together():
    with pytest.raises(ValueError, match="mutually exclusive"):
        HfDownloadRequest(repo_id="o/r", local_dir="C:\\models", cache_dir="C:\\cache")


def test_request_rejects_cache_target_with_local_dir():
    with pytest.raises(ValueError, match="Cache Only"):
        HfDownloadRequest(repo_id="o/r", target=HfTarget.CACHE, local_dir="C:\\models")


def test_request_requires_custom_folder_path():
    with pytest.raises(ValueError, match="Custom Folder"):
        HfDownloadRequest(repo_id="o/r", target=HfTarget.CUSTOM)


def test_request_rejects_non_positive_workers():
    with pytest.raises(ValueError, match="positive integer"):
        HfDownloadRequest(repo_id="o/r", max_workers=0)


def test_request_is_immutable_snapshot():
    request = HfDownloadRequest(repo_id="o/r")
    with pytest.raises(FrozenInstanceError):
        request.repo_id = "other/repo"  # type: ignore[misc]


def test_request_describe_covers_selection_modes():
    entire = HfDownloadRequest(repo_id="o/r")
    assert "entire repository" in entire.describe()
    exact = HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.EXACT, filenames=["m-Q4.gguf"])
    assert "m-Q4.gguf" in exact.describe()
    patterns = HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.PATTERNS, include=["*.gguf"], exclude=["*.bin"], revision="main")
    assert "include *.gguf" in patterns.describe() and "exclude *.bin" in patterns.describe() and "rev main" in patterns.describe()


def test_request_refreshes_selectors_only_for_configured_targets():
    assert HfDownloadRequest(repo_id="o/r", target=HfTarget.MODELS).refreshes_selectors
    assert HfDownloadRequest(repo_id="o/r", target=HfTarget.CACHE).refreshes_selectors is False
    assert HfDownloadRequest(repo_id="o/r", target=HfTarget.CUSTOM, local_dir="C:\\x").refreshes_selectors is False

# ---------------------------------------------------------------------------
# argv builder
# ---------------------------------------------------------------------------

CAPS = HfCliCapabilities(path="hf.exe", hub_version="9.9.9")


def test_build_download_argv_omits_repo_type_for_model():
    request = HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.EXACT, filenames=["a.gguf"], local_dir="C:\\models")
    argv = build_download_argv(CAPS, request)
    assert "--repo-type" not in argv
    assert argv[:3] == ["hf.exe", "download", "o/r"]
    assert "a.gguf" in argv


@pytest.mark.parametrize(("repo_type", "expected"), ((HfRepoType.DATASET, "dataset"), (HfRepoType.SPACE, "space")))
def test_build_download_argv_repo_type(repo_type, expected):
    request = HfDownloadRequest(repo_id="o/r", repo_type=repo_type)
    argv = build_download_argv(CAPS, request)
    assert argv[argv.index("--repo-type") + 1] == expected



def _track(states: dict[str, HfJobState], job_id: str, state) -> None:
    states[job_id] = state

def test_build_download_argv_revision():
    assert "--revision" not in build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r"))
    argv = build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r", revision="main"))
    assert argv[argv.index("--revision") + 1] == "main"


def test_build_download_argv_exact_files_are_positional():
    argv = build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.EXACT, filenames=["a.gguf", "b.gguf"]))
    assert argv[:5] == ["hf.exe", "download", "o/r", "a.gguf", "b.gguf"]


def test_build_download_argv_include_exclude_repeat_flag():
    argv = build_download_argv(
        CAPS,
        HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.PATTERNS, include=["*.gguf", "*.bin"], exclude=["README.md"]),
    )
    assert argv.count("--include") == 2
    assert argv[argv.index("--include") + 1] == "*.gguf"
    assert argv[argv.index("--include") + 3] == "*.bin"
    assert argv[argv.index("--exclude") + 1] == "README.md"


def test_build_download_argv_local_dir_and_cache_only():
    to_folder = build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r", local_dir="C:\\models"))
    assert to_folder[to_folder.index("--local-dir") + 1] == "C:\\models"
    assert "--cache-dir" not in to_folder
    cache_only = build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r", target=HfTarget.CACHE, cache_dir="C:\\cache"))
    assert "--local-dir" not in cache_only
    assert cache_only[cache_only.index("--cache-dir") + 1] == "C:\\cache"


def test_build_download_argv_force_download():
    assert "--force-download" not in build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r"))
    assert "--force-download" in build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r", force_download=True))


def test_build_download_argv_workers_omitted_by_default():
    assert "--max-workers" not in build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r"))
    argv = build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r", max_workers=4))
    assert argv[argv.index("--max-workers") + 1] == "4"


def test_build_download_argv_dry_run_uses_quiet():
    argv = build_download_argv(CAPS, HfDownloadRequest(repo_id="o/r"), dry_run=True)
    assert argv[-2:] == ["--dry-run", "--quiet"]


def test_build_download_argv_rejects_unsupported_options():
    caps = HfCliCapabilities(path="hf.exe", dry_run=False)
    with pytest.raises(HfCliError, match="--dry-run"):
        build_download_argv(caps, HfDownloadRequest(repo_id="o/r"), dry_run=True)
    caps = HfCliCapabilities(path="hf.exe", force_download=False)
    with pytest.raises(HfCliError, match="--force-download"):
        build_download_argv(caps, HfDownloadRequest(repo_id="o/r", force_download=True))

def test_parse_hub_version():
    assert parse_hub_version("huggingface_hub version: 0.36.2") == "0.36.2"
    assert parse_hub_version("nope") == ""


def test_parse_whoami_authenticated():
    assert parse_whoami("user:  testuser") == "testuser"


def test_parse_whoami_unauthenticated():
    assert parse_whoami("You are not logged in.") == ""


def test_parse_download_help_records_capabilities():
    help_text = (
        "usage: hf download [repo] [file] [options]\n"
        "  --repo-type [model|dataset|space]\n"
        "  --revision REVISION\n"
        "  --include [INCLUDE ...]\n"
        "  --exclude [EXCLUDE ...]\n"
        "  --cache-dir CACHE_DIR\n"
        "  --local-dir LOCAL_DIR\n"
        "  --force-download\n"
        "  --max-workers [1-64]\n"
    )
    flags = parse_download_help(help_text)
    assert flags["local_dir"] and flags["repo_type"] and flags["max_workers"]
    assert flags["dry_run"] is False


def test_parse_dry_run_1x_format():
    text = (
        "[dry-run] Will download 2 files (out of 3) totalling 1.2 GB.\n"
        "\n"
        "File                   Bytes to download\n"
        "---------------------  -----------------\n"
        "model-Q4_K_M.gguf      1.2 GB\n"
        "config.json            -\n"
        "README.md              1.1 KB\n"
    )
    report = parse_dry_run(text, 0)
    assert report.parsed
    assert report.total_files == 3
    assert report.transfer_files == 2
    assert report.transfer_text == "1.2 GB"
    assert [file.filename for file in report.files] == ["model-Q4_K_M.gguf", "config.json", "README.md"]
    assert [file.will_download for file in report.files] == [True, False, True]
    assert report.files[0].size_text == "1.2 GB"


def test_parse_dry_run_unrecognized_output_stays_raw():
    report = parse_dry_run("some unrecognizable output", 0)
    assert report.parsed is False
    assert report.raw == "some unrecognizable output"
    assert report.files == ()


def test_render_command_line_quotes_paths():
    rendered = render_command_line(["C:\\tools hf\\hf.exe", "download", "o/r", "--local-dir", "C:\\my models"])
    assert '"C:\\tools hf\\hf.exe"' in rendered
    assert '"C:\\my models"' in rendered
    assert "download o/r" in rendered


def test_redact_secrets_masks_token():
    assert SENTINEL not in redact_secrets(f"token={SENTINEL} used")
    assert "hf_***" in redact_secrets(f"token={SENTINEL} used")
    assert redact_secrets("normal text") == "normal text"


def test_snapshot_lists_relative_file_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sub").mkdir()
        (root / "a.gguf").write_bytes(b"x")
        (root / "sub" / "b.bin").write_bytes(b"y")
        assert _snapshot(root) == {"a.gguf", "sub/b.bin"}
    assert _snapshot(Path(tempfile.gettempdir()) / "definitely-missing-hf-test-dir") == set()


def test_locate_hf_cli_returns_none_when_absent(monkeypatch):
    monkeypatch.setenv("PATH", str(Path(tempfile.gettempdir()) / "empty-hf-bin"))
    assert locate_hf_cli() is None


# ---------------------------------------------------------------------------
# Service (fake hf executables, offscreen)
# ---------------------------------------------------------------------------


def _spin_until(predicate, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("event loop did not reach the expected state")
        QApplication.processEvents()
        time.sleep(0.01)


_HELP_WITH_DRY_RUN = (
    "  --repo-type REPO_TYPE\n"
    "  --revision REVISION\n"
    "  --include INCLUDE\n"
    "  --exclude EXCLUDE\n"
    "  --cache-dir CACHE_DIR\n"
    "  --local-dir LOCAL_DIR\n"
    "  --local-dir-use-symlinks LINKS\n"
    "  --force-download\n"
    "  --max-workers MAX_WORKERS\n"
    "  --dry-run\n"
    "  --quiet\n"
)

_HELP_NO_DRY_RUN = (
    "  --repo-type REPO_TYPE\n"
    "  --revision REVISION\n"
    "  --include INCLUDE\n"
    "  --exclude EXCLUDE\n"
    "  --cache-dir CACHE_DIR\n"
    "  --local-dir LOCAL_DIR\n"
    "  --force-download\n"
    "  --max-workers MAX_WORKERS\n"
    "  --quiet\n"
)

_DRY_RUN_OUTPUT = (
    "[dry-run] Will download 2 files (out of 3) totalling 1.2 GB.\r\n"
    "\r\n"
    "File                   Bytes to download\r\n"
    "---------------------  -----------------\r\n"
    "model-Q4_K_M.gguf      1.2 GB\r\n"
    "config.json            -\r\n"
    "README.md              1.1 KB\r\n"
)


def _write_fake_hf(directory: Path, *, with_dry_run: bool = True, authenticated: bool = True) -> Path:
    """Write a fake ``hf`` batch CLI.

    Uses goto labels instead of nested parenthesized if-blocks: cmd.exe
    silently drops the exit code of ``exit /b N`` when it sits inside a
    block that is itself nested in another block.
    """
    whoami = "echo user:  testuser\r\nexit /b 0" if authenticated else "echo You are not logged in.\r\nexit /b 1"
    help_block = "\r\n".join(f"echo {line}" for line in (_HELP_WITH_DRY_RUN if with_dry_run else _HELP_NO_DRY_RUN).splitlines())
    dry_block = "\r\n".join("echo." if not line else f"echo {line}" for line in _DRY_RUN_OUTPUT.splitlines())
    default_download = (
        f"{sys.executable} -c \"import sys; from pathlib import Path; a = sys.argv[1:]; i = a.index('--local-dir'); p = Path(a[i+1]); p.mkdir(parents=True, exist_ok=True); (p / 'fake-model.gguf').write_text('x')\" %*"
    )
    body = (
        "@echo off\r\n"
        "if \"%1\"==\"version\" goto :version\r\n"
        "if \"%1\"==\"auth\" if \"%2\"==\"whoami\" goto :whoami\r\n"
        "if \"%1\"==\"download\" goto :download\r\n"
        "exit /b 0\r\n"
        ":version\r\n"
        "echo huggingface_hub version: 9.9.9\r\n"
        "exit /b 0\r\n"
        ":whoami\r\n"
        f"{whoami}\r\n"
        ":download\r\n"
        "if \"%2\"==\"--help\" goto :download_help\r\n"
        "for %%A in (%*) do if \"%%A\"==\"--dry-run\" set DRYRUN=1\r\n"
        "if defined DRYRUN goto :dry_run\r\n"
        "if \"%2\"==\"o/slow\" goto :slow\r\n"
        "if \"%2\"==\"o/fail\" goto :fail\r\n"
        "if \"%2\"==\"o/noop\" goto :noop\r\n"
        "if \"%2\"==\"o/leak\" goto :leak\r\n"
        f"{default_download}\r\n"
        "exit /b 0\r\n"
        ":download_help\r\n"
        f"{help_block}\r\n"
        "exit /b 0\r\n"
        ":dry_run\r\n"
        f"{dry_block}\r\n"
        "exit /b 0\r\n"
        ":slow\r\n"
        f"{sys.executable} -c \"import time; time.sleep(30)\"\r\n"
        "exit /b 0\r\n"
        ":fail\r\n"
        "echo ERROR: Repository Not Found\r\n"
        "exit /b 4\r\n"
        ":noop\r\n"
        "echo nothing new here\r\n"
        "exit /b 0\r\n"
        ":leak\r\n"
        f"echo {SENTINEL}\r\n"
        "exit /b 0\r\n"
    )
    bat = directory / "hf.bat"
    bat.write_text(body, encoding="ascii")
    return bat


@pytest.fixture
def fake_hf_path(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("PATH", f"{tmp}{os.pathsep}{os.environ['PATH']}")
        yield Path(tmp)


@pytest.fixture(autouse=True)
def _shutdown_services():
    """Tear down services before their QProcess children are destroyed.

    Destroying a QProcess that is still running can abort the whole
    process on Windows, so explicitly stop every service and drain the
    event loop until no child process is left.
    """
    created: list[HfCliService] = []
    original_init = HfCliService.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    HfCliService.__init__ = tracking_init

    def still_running(service: HfCliService) -> bool:
        for process in (service._process, service._preview_process):
            if process is None:
                continue
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    return True
            except RuntimeError:
                continue  # C++ object already deleted
        return False

    try:
        yield
    finally:
        HfCliService.__init__ = original_init
        for service in created:
            try:
                service.shutdown()
            except RuntimeError:
                pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(still_running(service) for service in created):
            QApplication.processEvents()
            time.sleep(0.01)


def test_probe_detects_version_auth_and_capabilities(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    infos: list = []
    caps: list = []
    auths: list = []
    service.info_ready.connect(infos.append)
    service.capabilities_ready.connect(caps.append)
    service.auth_ready.connect(auths.append)
    service.probe()
    _spin_until(lambda: infos and caps and auths)
    assert infos[-1].path.lower().endswith("hf.bat")
    assert infos[-1].hub_version == "9.9.9"
    assert caps[-1].dry_run is True
    assert caps[-1].local_dir is True
    assert auths[-1].authenticated is True
    assert auths[-1].username == "testuser"
    assert service.dry_run_supported


def test_probe_reports_unauthenticated(fake_hf_path):
    _write_fake_hf(fake_hf_path, authenticated=False)
    service = HfCliService()
    auths: list = []
    service.auth_ready.connect(auths.append)
    service.probe()
    _spin_until(lambda: auths)
    assert auths[-1].authenticated is False
    assert auths[-1].label.startswith("Not authenticated")


def test_probe_without_hf_emits_empty_status(monkeypatch):
    monkeypatch.setenv("PATH", str(Path(tempfile.gettempdir()) / "empty-hf-bin"))
    service = HfCliService()
    infos: list = []
    service.info_ready.connect(infos.append)
    service.probe()
    _spin_until(lambda: infos)
    assert infos[-1].path == ""
    assert service.capabilities is not None and service.capabilities.path == ""
    assert service.dry_run_supported is False


def test_download_exit_zero_with_new_files_is_success(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    results: list[HfDownloadResult] = []
    service.job_finished.connect(lambda job_id, result: results.append(result))
    local = fake_hf_path / "models"
    job_id = service.request_download(HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.EXACT, filenames=["fake-model.gguf"], local_dir=str(local)))
    _spin_until(lambda: len(results) == 1)
    result = results[0]
    assert service.job(job_id).state is HfJobState.COMPLETED
    assert result.ok is True
    assert result.exit_code == 0
    assert result.new_files == ("fake-model.gguf",)
    assert (local / "fake-model.gguf").exists()


def test_download_exit_zero_without_new_files_is_neutral_success(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    results: list[HfDownloadResult] = []
    service.job_finished.connect(lambda job_id, result: results.append(result))
    local = fake_hf_path / "m"
    local.mkdir()
    (local / "existing.gguf").write_bytes(b"x")
    service.request_download(HfDownloadRequest(repo_id="o/noop", local_dir=str(local)))
    _spin_until(lambda: len(results) == 1)
    result = results[0]
    assert result.ok is True
    assert result.exit_code == 0
    assert result.new_files == ()
    assert "no new pathnames were created" in result.detail


def test_download_nonzero_exit_is_failure_with_output_tail(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    results: list[HfDownloadResult] = []
    service.job_finished.connect(lambda job_id, result: results.append(result))
    service.request_download(HfDownloadRequest(repo_id="o/fail", local_dir=str(fake_hf_path / "m")))
    _spin_until(lambda: len(results) == 1)
    result = results[0]
    assert result.ok is False
    assert result.exit_code == 4
    assert "Repository Not Found" in result.detail


def test_secret_never_reaches_output_signal_or_result(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    lines: list[str] = []
    results: list[HfDownloadResult] = []
    service.job_output.connect(lambda job_id, line: lines.append(line))
    service.job_finished.connect(lambda job_id, result: results.append(result))
    service.request_download(HfDownloadRequest(repo_id="o/leak", local_dir=str(fake_hf_path / "m")))
    _spin_until(lambda: len(results) == 1)
    joined = "\n".join(lines) + results[0].output + results[0].detail
    assert SENTINEL not in joined
    assert "hf_***" in joined


def test_enqueue_while_active_keeps_single_active_and_queues_rest(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    states: dict[str, HfJobState] = {}
    service.job_state_changed.connect(lambda job_id, state: _track(states, job_id, state))
    slow_id = service.request_download(HfDownloadRequest(repo_id="o/slow", local_dir=str(fake_hf_path / "m1")))
    _spin_until(lambda: states.get(slow_id) == HfJobState.DOWNLOADING)
    fast_id = service.request_download(HfDownloadRequest(repo_id="o/r", local_dir=str(fake_hf_path / "m2")))
    assert states.get(fast_id) == HfJobState.QUEUED
    assert service.busy
    assert list(service._queue) == [fast_id]
    service.cancel_active()
    _spin_until(lambda: states.get(slow_id) == HfJobState.CANCELLED and states.get(fast_id) == HfJobState.COMPLETED, timeout=60)
    assert states[slow_id] == HfJobState.CANCELLED
    assert states[fast_id] == HfJobState.COMPLETED


def test_cancel_active_leaves_queued_jobs_intact(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    states: dict[str, HfJobState] = {}
    service.job_state_changed.connect(lambda job_id, state: _track(states, job_id, state))
    slow_id = service.request_download(HfDownloadRequest(repo_id="o/slow", local_dir=str(fake_hf_path / "m1")))
    _spin_until(lambda: states.get(slow_id) == HfJobState.DOWNLOADING)
    second_id = service.request_download(HfDownloadRequest(repo_id="o/r", local_dir=str(fake_hf_path / "m2")))
    third_id = service.request_download(HfDownloadRequest(repo_id="o/r", local_dir=str(fake_hf_path / "m3")))
    assert list(service._queue) == [second_id, third_id]
    service.cancel_active()
    _spin_until(lambda: states.get(slow_id) == HfJobState.CANCELLED)
    # The pump starts the next queued job; nothing is dropped or reordered.
    assert list(service._queue) == [third_id]
    _spin_until(lambda: states.get(second_id) == HfJobState.COMPLETED, timeout=60)
    _spin_until(lambda: states.get(third_id) == HfJobState.COMPLETED, timeout=60)


def test_retry_failed_job(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    states: dict[str, HfJobState] = {}
    failures: list[HfDownloadResult] = []
    service.job_state_changed.connect(lambda job_id, state: _track(states, job_id, state))
    service.job_finished.connect(lambda job_id, result: failures.append(result))
    fail_id = service.request_download(HfDownloadRequest(repo_id="o/fail", local_dir=str(fake_hf_path / "m")))
    _spin_until(lambda: len(failures) == 1)
    assert states.get(fail_id) == HfJobState.FAILED
    assert failures[0].exit_code == 4
    # Retry re-queues the same immutable snapshot.
    assert service.retry(fail_id)
    assert service._active == fail_id  # nothing else running, so the retry pumps immediately
    # o/fail always exits 4, so it fails again — the point is the re-queue mechanic.
    _spin_until(lambda: len(failures) == 2)
    assert failures[1].exit_code == 4
    assert states.get(fail_id) == HfJobState.FAILED
def test_remove_queued_and_move_queued(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    service.request_download(HfDownloadRequest(repo_id="o/slow", local_dir=str(fake_hf_path / "m1")))
    _spin_until(lambda: service.busy)
    a = service.request_download(HfDownloadRequest(repo_id="o/r", local_dir=str(fake_hf_path / "a")))
    b = service.request_download(HfDownloadRequest(repo_id="o/r", local_dir=str(fake_hf_path / "b")))
    c = service.request_download(HfDownloadRequest(repo_id="o/r", local_dir=str(fake_hf_path / "c")))
    assert list(service._queue) == [a, b, c]
    assert service.move_queued(c, -1)
    assert list(service._queue) == [a, c, b]
    assert service.move_queued(c, -1)
    assert list(service._queue) == [c, a, b]
    assert service.move_queued(c, -1) is False
    assert service.remove_queued(a)
    assert list(service._queue) == [c, b]
    service.cancel_active()
    _spin_until(lambda: not service.busy, timeout=30)



def test_preview_parses_dry_run_output(fake_hf_path):
    _write_fake_hf(fake_hf_path)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    reports: list = []
    service.preview_finished.connect(reports.append)
    service.preview(HfDownloadRequest(repo_id="o/r", selection_mode=HfSelectionMode.EXACT, filenames=["fake-model.gguf"], local_dir=str(fake_hf_path / "m")))
    _spin_until(lambda: reports)
    report = reports[0]
    assert report.exit_code == 0
    assert report.parsed
    assert report.total_files == 3
    assert report.transfer_files == 2
    assert report.transfer_text == "1.2 GB"
    assert [file.filename for file in report.files][:2] == ["model-Q4_K_M.gguf", "config.json"]
    # Dry run must never mutate the destination.
    assert not (fake_hf_path / "m" / "fake-model.gguf").exists()


def test_preview_rejected_when_cli_lacks_dry_run(fake_hf_path):
    _write_fake_hf(fake_hf_path, with_dry_run=False)
    service = HfCliService()
    service.probe()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    assert service.capabilities.dry_run is False
    assert service.dry_run_supported is False
    with pytest.raises(HfCliError, match="--dry-run"):
        service.preview(HfDownloadRequest(repo_id="o/r"))
