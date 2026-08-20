"""Hermetic service tests for staged server verification (fake llama-server)."""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

QApplication.instance() or QApplication([])

from app.models.command import Command, CommandArgument
from app.models.server_verification import VerificationMode
from app.services.command_runner import CommandRunner
from app.services.flag_catalog import FlagCatalog
from app.services.server_verification_service import ServerVerificationService, resolve_api_keys

from qt_utils import _spin_until

SENTINEL = "sv_SUPERSECRET_9f8e7d6c5b4a3210"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog() -> FlagCatalog:
    return FlagCatalog.load_bundled(Path(__file__).resolve().parent.parent / "data" / "llama_server_flags.json")


@pytest.fixture
def fake_server(monkeypatch) -> Path:
    """A temp dir containing a hermetic ``llama-server.bat``."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copy(Path(__file__).parent / "fake_llama_server.py", root / "fake_llama_server.py")
        python = os.environ.get("FAKE_PYTHON", shutil.which("python") or "python")
        bat = root / "llama-server.bat"
        bat.write_text(f'@echo off\r\n"{python}" "%~dp0fake_llama_server.py" %*\r\n', encoding="ascii")
        yield root


@pytest.fixture(autouse=True)
def _teardown_runners():
    """Stop every CommandRunner's process (fake pythons are PID-killed by the helper)."""
    created: list[CommandRunner] = []
    original = CommandRunner.__init__

    def tracking(self, *args, **kwargs):
        original(self, *args, **kwargs)
        created.append(self)

    CommandRunner.__init__ = tracking
    try:
        yield
    finally:
        CommandRunner.__init__ = original
        for runner in created:
            try:
                runner.stop()
            except RuntimeError:
                pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if all(runner.process.state() == QProcess.ProcessState.NotRunning for runner in created):
                break
            QApplication.processEvents()
            time.sleep(0.01)


def pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def build_command(*, port: int, model: Path, extra: tuple[tuple[str, list[str]], ...] = ()) -> Command:
    command = Command(executable="llama-server")
    command.arguments[0].values = [str(model)]
    command.arguments.append(CommandArgument("--port", [str(port)]))
    for flag, values in extra:
        command.arguments.append(CommandArgument(flag, list(values)))
    return command


def run_verification(
    fake_server: Path,
    catalog: FlagCatalog,
    command: Command,
    tmp_path: Path,
    *,
    timeout_s: int = 45,
    **env: str,
) -> tuple[list, CommandRunner, list[str]]:
    """Launch verification, wait for completion, then clean up process + orphans."""
    runner = CommandRunner()
    service = ServerVerificationService(runner)
    results: list = []
    console: list[str] = []
    service.completed.connect(results.append)
    runner.output.connect(console.append)
    pid_file = tmp_path / "fake-server.pid"
    env["FAKE_PID_FILE"] = str(pid_file)
    for key, value in env.items():
        os.environ[key] = value
    try:
        service.verify(command, catalog, executable=str(fake_server / "llama-server.bat"), timeout_seconds=timeout_s)
        _spin_until(lambda: results, timeout=90)
        return results, runner, console
    finally:
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 9)  # the fake python lingers after the cmd wrapper exits
        except (OSError, ValueError):
            pass
        service.cancel()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and runner.process.state() != QProcess.ProcessState.NotRunning:
            QApplication.processEvents()
            time.sleep(0.01)


# ---------------------------------------------------------------------------
# 1. Healthy generation server
# ---------------------------------------------------------------------------


def test_healthy_generation_server(fake_server, catalog, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    print("\nDETAIL:", repr(result.error_detail), flush=True)
    print("CONSOLE:", "; ".join(_console), flush=True)
    assert result.verified, repr(result)
    assert result.started and result.ready and result.api_ok and result.inference_ok is True
    assert result.failed_stage is None
    assert result.model_ids[0] == "fake-model"
    assert result.ready_ms is not None and result.inference_ms is not None
    assert "OK" in result.generated_text


# 2. Slow loading (503s then ready)


def test_slow_loading_server_becomes_ready(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    monkeypatch.setenv("FAKE_SLOW_503S", "3")
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    assert results[0].verified
    assert results[0].ready
    assert results[0].model_ids == ("fake-model",)


# 3. Readiness timeout


def test_readiness_timeout_fails_stage_ready(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    monkeypatch.setenv("FAKE_SLOW_503S", "99999")
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path, timeout_s=2)
    result = results[0]
    assert result.failed_stage == "ready"
    assert "did not become ready within 2 seconds" in result.error_detail
    assert "fake server" in result.log_tail  # useful bounded log tail


# 4. Process exits before readiness


def test_process_exit_before_ready(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    monkeypatch.setenv("FAKE_EXIT_EARLY", "1")
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.failed_stage in {"ready", "process"}
    assert result.exit_code == 3
    assert "exited with code 3" in result.error_detail


# 5. Port already occupied: fail before launch


def test_occupied_port_fails_before_launch(fake_server, catalog, tmp_path):
    occupied = pick_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", occupied))
    blocker.listen(1)
    try:
        model = tmp_path / "model.gguf"
        model.write_bytes(b"x")
        command = build_command(port=occupied, model=model)
        results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    finally:
        blocker.close()
    result = results[0]
    assert result.failed_stage == "preflight"
    assert "already in use" in result.error_detail
    assert result.started is False


# 6. Malformed /v1/models


def test_malformed_models_fails_stage3(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    monkeypatch.setenv("FAKE_MALFORMED", "models")
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.failed_stage == "api"
    assert "invalid JSON" in result.error_detail


# 7/8. --alias matching


def test_alias_matches(fake_server, catalog, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model, extra=(("--alias", ["wanted-alias"]),))
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.verified
    assert result.model_ids == ("wanted-alias",)


def test_alias_missing_fails_stage3(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model, extra=(("--alias", ["wanted-alias"]),))
    monkeypatch.setenv("FAKE_MODEL_ID", "something-else")
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.failed_stage == "api"
    assert "wanted-alias" in result.error_detail


# 9. API key authenticated + secret never visible


def test_api_key_used_and_never_leaked(fake_server, catalog, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model, extra=(("--api-key", [SENTINEL]),))
    results, _runner, console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.verified
    joined = "\n".join(console) + result.error_detail + result.log_tail + "".join(st.detail for st in result.stages)
    assert SENTINEL not in joined
    assert "***" in "\n".join(console)  # rendered argv masks --api-key values


# 10. API prefix


def test_api_prefix_honored(fake_server, catalog, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model, extra=(("--api-prefix", ["/v1"]),))
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.verified
    assert result.api_prefix == "/v1"


# 11. timing fields parsed


def test_timing_fields_parsed(fake_server, catalog, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.verified
    assert result.prompt_tokens == 9
    assert result.completion_tokens == 2
    assert result.generation_tps == pytest.approx(34.8)
    assert result.prompt_tps == pytest.approx(5.0)


# 12. missing timing fields


def test_missing_timings_still_succeeds(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    monkeypatch.setenv("FAKE_NO_TIMINGS", "1")
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.verified
    assert result.prompt_tokens is None
    assert result.generation_tps is None
    assert "OK" in result.generated_text


# 13. embedding mode


def test_embedding_mode_uses_embedding_endpoint(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    trace = tmp_path / "trace.txt"
    command = build_command(port=pick_port(), model=model, extra=(("--embeddings", []),))
    monkeypatch.setenv("FAKE_TRACE", str(trace))
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.verified
    assert result.mode is VerificationMode.EMBEDDING
    paths = trace.read_text(encoding="utf-8")
    assert "/embedding" in paths
    assert "/completion" not in paths


# 14. rerank mode


def test_rerank_mode_uses_rerank_endpoint(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    trace = tmp_path / "trace.txt"
    command = build_command(port=pick_port(), model=model, extra=(("--rerank", []),))
    monkeypatch.setenv("FAKE_TRACE", str(trace))
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.verified
    assert result.mode is VerificationMode.RERANK
    assert "/rerank" in trace.read_text(encoding="utf-8")


# 15. unsupported mode -> Stage 4 SKIPPED


def test_unsupported_mode_skips_stage4(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    monkeypatch.setenv("FAKE_MODE", "unsupported")  # /completion -> 404
    results, _runner, _console = run_verification(fake_server, catalog, command, tmp_path)
    result = results[0]
    assert result.failed_stage is None
    assert result.skipped_stage == "inference"
    assert result.inference_ok is None
    assert result.api_ok is True


# 16. Stop during readiness


def test_stop_during_readiness_aborts(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    monkeypatch.setenv("FAKE_SLOW_503S", "99999")
    runner = CommandRunner()
    service = ServerVerificationService(runner)
    completed: list = []
    service.completed.connect(completed.append)
    service.verify(command, catalog, executable=str(fake_server / "llama-server.bat"), timeout_seconds=600)
    _spin_until(lambda: service.active and runner.process.state() != QProcess.ProcessState.NotRunning)
    service.cancel()
    _spin_until(lambda: runner.process.state() == QProcess.ProcessState.NotRunning, timeout=30)
    QApplication.processEvents()
    assert completed == []
    assert service.active is False


# 17. stale run cannot mutate a newer run


def test_stale_run_cannot_override_newer(fake_server, catalog, tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    port_a, port_b = pick_port(), pick_port()
    runner = CommandRunner()
    service = ServerVerificationService(runner)
    completed: list = []
    service.completed.connect(completed.append)
    command_a = build_command(port=port_a, model=model)
    command_b = build_command(port=port_b, model=model)
    monkeypatch.setenv("FAKE_MODEL_ID", "old-model")
    monkeypatch.setenv("FAKE_SLOW_503S", "99999")
    service.verify(command_a, catalog, executable=str(fake_server / "llama-server.bat"), timeout_seconds=600)
    QApplication.processEvents()
    monkeypatch.setenv("FAKE_MODEL_ID", "new-model")
    monkeypatch.setenv("FAKE_SLOW_503S", "0")
    service.verify(command_b, catalog, executable=str(fake_server / "llama-server.bat"), timeout_seconds=30)
    _spin_until(lambda: len(completed) == 1, timeout=60)
    QApplication.processEvents()
    time.sleep(1.0)
    QApplication.processEvents()
    assert len(completed) == 1
    assert completed[0].model_ids == ("new-model",)
    service.cancel()


# 17. api-key-file resolution


def test_api_key_file_resolution_and_unreadable_file(catalog, tmp_path):
    key_file = tmp_path / "keys.txt"
    key_file.write_text("# comment\nfirst-key\nsecond-key\n", encoding="utf-8")
    command = Command(executable="llama-server")
    command.arguments.append(CommandArgument("--api-key-file", [str(key_file)]))
    keys = resolve_api_keys(command, catalog)
    assert keys == ("first-key",)
    missing = tmp_path / "missing-keys.txt"
    command2 = Command(executable="llama-server")
    command2.arguments.append(CommandArgument("--api-key-file", [str(missing)]))
    with pytest.raises(OSError):
        resolve_api_keys(command2, catalog)


# 18. cancel leaves no running server


def test_cancel_terminates_process(fake_server, catalog, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    command = build_command(port=pick_port(), model=model)
    runner = CommandRunner()
    service = ServerVerificationService(runner)
    completed: list = []
    service.completed.connect(completed.append)
    service.verify(command, catalog, executable=str(fake_server / "llama-server.bat"), timeout_seconds=30)
    _spin_until(lambda: completed, timeout=60)
    assert completed[0].verified
    assert runner.running  # verified servers stay running until Stop
    service.cancel()
    _spin_until(lambda: not runner.running, timeout=30)
    QApplication.processEvents()
    assert completed[0].verified