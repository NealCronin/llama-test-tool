from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

QApplication.instance() or QApplication([])
from app.models.hf_download import HfDownloadRequest, HfTarget, HfDownloadResult
from app.services.hf_cli_service import HfCliError, HfCliService, build_download_argv, find_hf_cli, render_command_line, _snapshot


def test_request_normalization_strips_and_drops_blank_entries():
    request = HfDownloadRequest(
        repo_id="  owner/repo ",
        target=HfTarget.MODELS,
        filenames=[" a.gguf ", "", "  ", "b.gguf"],
        include=[" *.gguf "],
        exclude=[" "],
        revision=" main ",
        token=" tok ",
    )
    assert request.repo_id == "owner/repo"
    assert request.filenames == ["a.gguf", "b.gguf"]
    assert request.include == ["*.gguf"]
    assert request.exclude == []
    assert request.revision == "main"


def test_request_describe_covers_filename_and_glob_selection():
    by_name = HfDownloadRequest(repo_id="o/r", target=HfTarget.MODELS, filenames=["m-Q4.gguf"])
    assert by_name.describe() == "o/r → Models (m-Q4.gguf)"
    by_glob = HfDownloadRequest(repo_id="o/r", target=HfTarget.DRAFTERS, include=["*-Q8_0.gguf", "*.bin"])
    assert by_glob.describe() == "o/r → Drafters (--include *-Q8_0.gguf --include *.bin)"


def test_build_download_argv_groups_include_and_exclude_patterns():
    local = Path("C:\\models")
    request = HfDownloadRequest(
        repo_id="o/r",
        target=HfTarget.MODELS,
        filenames=["a.gguf", "b.gguf"],
        include=["*-Q4*.gguf", "*.json"],
        exclude=["README.md"],
        revision="main",
        token="hf_x",
        max_workers=4,
    )
    argv = build_download_argv("hf.exe", request, local)
    assert argv == [
        "hf.exe", "download", "o/r", "a.gguf", "b.gguf",
        "--revision", "main",
        "--include", "*-Q4*.gguf", "*.json",
        "--exclude", "README.md",
        "--local-dir", str(local),
        "--max-workers", "4",
        "--token", "hf_x",
        "--quiet",
    ]


def test_build_download_argv_omits_unset_options():
    request = HfDownloadRequest(repo_id="o/r", target=HfTarget.MODELS, filenames=["a.gguf"])
    argv = build_download_argv("hf.exe", request, Path("m"))
    assert "--revision" not in argv
    assert "--include" not in argv
    assert "--exclude" not in argv
    assert "--max-workers" not in argv
    assert "--token" not in argv
    assert argv[-1] == "--quiet"


def test_render_command_line_quotes_tokens_with_spaces():
    rendered = render_command_line(["C:\\tools hf\\hf.exe", "download", "o/r", "--local-dir", "C:\\my models"])
    assert '"C:\\tools hf\\hf.exe"' in rendered
    assert '"C:\\my models"' in rendered
    assert "download o/r" in rendered


def test_snapshot_lists_relative_file_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sub").mkdir()
        (root / "a.gguf").write_bytes(b"x")
        (root / "sub" / "b.bin").write_bytes(b"y")
        assert _snapshot(root) == {"a.gguf", "sub\\b.bin"}
    assert _snapshot(Path(tempfile.gettempdir()) / "definitely-missing-hf-test-dir") == set()


def test_find_hf_cli_raises_helpful_error_when_absent(monkeypatch):
    monkeypatch.setenv("PATH", str(Path(tempfile.gettempdir()) / "empty-hf-bin"))
    with pytest.raises(HfCliError, match="not found on PATH"):
        find_hf_cli()


def test_find_hf_cli_reads_hub_version(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "hf.bat"
        script.write_text("@echo off\r\necho huggingface_hub version: 0.36.2\r\n", encoding="ascii")
        monkeypatch.setenv("PATH", tmp)
        info = find_hf_cli()
    assert info.hub_version == "0.36.2"
    assert info.path.lower().endswith("hf.bat")


def _spin_until(predicate, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("event loop did not reach the expected state")
        QApplication.processEvents()
        time.sleep(0.01)


_VERSION_BLOCK = (
    '@echo off\r\n'
    'if "%1"=="version" (\r\n'
    "  echo huggingface_hub version: 9.9.9\r\n"
    "  exit /b 0\r\n"
    ")\r\n"
)


def _write_fake_hf(directory: Path, behavior: str) -> Path:
    if behavior == "success":
        body = _VERSION_BLOCK + (
            'echo fake hf progress line\r\n'
            f'{sys.executable} -c "import sys; from pathlib import Path; args = sys.argv[1:]; local = Path(args[args.index(\'--local-dir\') + 1]); (local / \'fake-model.gguf\').write_text(\'x\')" %*\r\n'
            'echo done on stdout\r\n'
            'exit /b 0\r\n'
        )
    elif behavior == "no_new_files":
        body = _VERSION_BLOCK + "echo nothing matched\r\nexit /b 0\r\n"
    elif behavior == "failure":
        body = _VERSION_BLOCK + "echo ERROR: Repository Not Found on stderr 1>&2\r\nexit /b 4\r\n"
    else:  # slow
        body = _VERSION_BLOCK + f'{sys.executable} -c "import time; time.sleep(30)"\r\nexit /b 0\r\n'
    bat = directory / "hf.bat"
    bat.write_text(body, encoding="ascii")
    return bat


@pytest.fixture
def fake_hf_path(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("PATH", f"{tmp}{os.pathsep}{os.environ['PATH']}")
        yield Path(tmp)


def test_service_downloads_and_detects_new_files(fake_hf_path):
    _write_fake_hf(fake_hf_path, "success")
    service = HfCliService()
    events: list[str] = []
    results: list[HfDownloadResult] = []
    service.state_changed.connect(lambda request_id, state: events.append(state))
    service.finished.connect(lambda request_id, result: results.append(result))
    drained: list[bool] = []
    service.all_finished.connect(lambda had_success: drained.append(had_success))
    local = fake_hf_path / "models"
    service.enqueue(HfDownloadRequest(repo_id="o/r", target=HfTarget.MODELS, filenames=["fake-model.gguf"]), local)
    _spin_until(lambda: drained)
    assert drained == [True]
    assert "Downloading" in events
    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.files == ["fake-model.gguf"]
    assert (local / "fake-model.gguf").exists()


def test_service_reports_error_output_on_failure(fake_hf_path):
    _write_fake_hf(fake_hf_path, "failure")
    service = HfCliService()
    results: list[HfDownloadResult] = []
    service.finished.connect(lambda request_id, result: results.append(result))
    drained: list[bool] = []
    service.all_finished.connect(lambda had_success: drained.append(had_success))
    service.enqueue(HfDownloadRequest(repo_id="o/missing", target=HfTarget.MODELS, filenames=["x.gguf"]), fake_hf_path / "m")
    _spin_until(lambda: drained)
    assert drained == [False]
    result = results[0]
    assert result.success is False
    assert "Repository Not Found" in result.detail


def test_service_flags_successful_run_without_new_files(fake_hf_path):
    _write_fake_hf(fake_hf_path, "no_new_files")
    service = HfCliService()
    results: list[HfDownloadResult] = []
    service.finished.connect(lambda request_id, result: results.append(result))
    drained: list[bool] = []
    service.all_finished.connect(lambda had_success: drained.append(had_success))
    local = fake_hf_path / "m"
    local.mkdir()
    (local / "existing.gguf").write_bytes(b"x")
    service.enqueue(HfDownloadRequest(repo_id="o/r", target=HfTarget.MODELS, include=["*.gguf"]), local)
    _spin_until(lambda: drained)
    assert drained == [True]
    result = results[0]
    assert result.success is True
    assert result.files == []
    assert "no new files" in result.detail


def test_service_stop_cancels_queued_and_running(fake_hf_path):
    bat = fake_hf_path / "hf.bat"
    bat.write_text(
        '@echo off\r\n'
        'if "%1"=="version" (\r\n'
        "  echo huggingface_hub version: 9.9.9\r\n"
        "  exit /b 0\r\n"
        ")\r\n"
        f'{sys.executable} -c "import time; time.sleep(30)"\r\n'
        "exit /b 0\r\n",
        encoding="ascii",
    )
    service = HfCliService()
    states: list[str] = []
    service.state_changed.connect(lambda request_id, state: states.append(state))
    service.enqueue(HfDownloadRequest(repo_id="o/r", target=HfTarget.MODELS, filenames=["a.gguf"]), fake_hf_path / "m1")
    _spin_until(lambda: not service._queue)  # first item is now running
    service.enqueue(HfDownloadRequest(repo_id="o/r", target=HfTarget.MODELS, filenames=["b.gguf"]), fake_hf_path / "m2")
    assert service._queue
    service.stop()
    _spin_until(lambda: not service.busy, timeout=30)
    assert "Cancelled" in states
    assert not service.busy
