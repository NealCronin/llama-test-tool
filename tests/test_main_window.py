import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplitter

from app.main_window import MainWindow
from app.services.flag_catalog import FlagCatalog
from app.settings import AppSettings

app = QApplication.instance() or QApplication([])


def test_command_builder_page_contains_output_console(monkeypatch):
    # Never spawn the user's real hf CLI in a GUI-hierarchy test.
    monkeypatch.setattr("app.services.hf_cli_service.locate_hf_cli", lambda: None)
    window = MainWindow(AppSettings(), FlagCatalog(FlagCatalog.fallback_specs()))
    # The builder and the output console share a vertical splitter...
    splitter = window.builder.parentWidget()
    assert isinstance(splitter, QSplitter)
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert window.console.parentWidget() is splitter
    # ...that fills the Command Builder tab page.
    page = splitter.parentWidget()
    assert page is not None
    index = window.tabs.indexOf(page)
    assert index >= 0
    assert window.tabs.tabText(index) == "Command Builder"
    # Server process output reaches that console (runner -> console wiring).
    window.runner.output.emit("test-command-line")
    assert "test-command-line" in window.console.text.toPlainText()
    # There is exactly one output console in the whole window.
    assert window.findChildren(type(window.console)) == [window.console]
    # The builder controls (including Stop) remain on the same page and work.
    for button in (window.builder.test, window.builder.stop):
        ancestor = button
        while ancestor.parentWidget() is not None and ancestor.parentWidget() is not page:
            ancestor = ancestor.parentWidget()
        assert ancestor.parentWidget() is page
    stopped: list[int] = []
    window.builder.stop_requested.connect(lambda: stopped.append(1))
    window.builder.set_running(True)  # Stop is only clickable while a test run is active
    assert window.builder.stop.isEnabled()
    window.builder.stop.click()
    assert stopped == [1]
    window.builder.set_running(False)
    window.close()


def test_device_split_preset_swaps_cpu_moe_alias_without_duplicates(monkeypatch):
    monkeypatch.setattr("app.services.hf_cli_service.locate_hf_cli", lambda: None)
    catalog = FlagCatalog.load_bundled(Path(__file__).resolve().parent.parent / "data" / "llama_server_flags.json")
    window = MainWindow(AppSettings(), catalog)
    owned = ("--cpu-moe", "--n-cpu-moe")

    def canonical_flags() -> list[str]:
        names = []
        for argument in window.builder.command.arguments:
            spec = catalog.find(argument.flag)
            names.append(spec.canonical_name if spec is not None else argument.flag)
        return names

    # Command already carries the partial-count spelling.
    window._apply_preset_values({"--n-cpu-moe": ["10"]}, owned=owned)
    # All-MoE is now checked: the other spelling must replace it, not stack on it.
    window._apply_preset_values({"--cpu-moe": []}, owned=owned)
    flags = canonical_flags()
    assert flags.count("--cpu-moe") == 1
    assert "--n-cpu-moe" not in flags
    # Back to a partial count: the bare flag is removed again.
    window._apply_preset_values({"--n-cpu-moe": ["4"]}, owned=owned)
    flags = canonical_flags()
    assert flags.count("--n-cpu-moe") == 1
    assert "--cpu-moe" not in flags
    window.close()


# ---------------------------------------------------------------------------
# Server verification: real signal path through MainWindow + fake server
# ---------------------------------------------------------------------------

import shutil
import socket
import tempfile
import time

import pytest

from app.models.command import CommandArgument
from app.services.llama_cpp_installation import LlamaCppInstallationService
from test_hf_cli_service import _spin_until
from test_server_verification import fake_server, _teardown_runners  # noqa: F401


@pytest.fixture(autouse=True)
def _ui_runners_teardown(_teardown_runners):
    return None


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _launch_window(tmp_path, monkeypatch, bat: Path, *, timeout_s: int = 180):
    monkeypatch.setattr("app.services.hf_cli_service.locate_hf_cli", lambda: None)
    monkeypatch.setattr(
        LlamaCppInstallationService,
        "active_server",
        staticmethod(lambda _settings: str(bat)),
    )
    settings = AppSettings(server_ready_timeout=timeout_s)
    catalog = FlagCatalog.load_bundled(Path(__file__).resolve().parent.parent / "data" / "llama_server_flags.json")
    window = MainWindow(settings, catalog)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    window.builder.command.arguments[0].values = [str(model)]
    window.show()
    QApplication.processEvents()
    return window


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _launch_window(tmp_path, monkeypatch, fake_server: Path, *, timeout_s: int = 180) -> MainWindow:
    monkeypatch.setattr("app.services.hf_cli_service.locate_hf_cli", lambda: None)
    monkeypatch.setattr(
        LlamaCppInstallationService,
        "active_server",
        staticmethod(lambda _settings: str(fake_server / "llama-server.bat")),
    )
    monkeypatch.setenv("FAKE_PID_FILE", str(tmp_path / "fake-server.pid"))
    settings = AppSettings(server_ready_timeout=timeout_s)
    catalog = FlagCatalog.load_bundled(Path(__file__).resolve().parent.parent / "data" / "llama_server_flags.json")
    window = MainWindow(settings, catalog)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    window.builder.command.arguments[0].values = [str(model)]
    window.show()
    QApplication.processEvents()
    return window


def _stop_server(window, tmp_path: Path) -> None:
    """Stop the server and kill the fake's orphaned python child."""
    window.builder.stop.click()
    _spin_until(lambda: not window.runner.running, timeout=30)
    try:
        os.kill(int((tmp_path / "fake-server.pid").read_text(encoding="utf-8").strip()), 9)
    except (OSError, ValueError):
        pass
    try:
        os.kill(int((tmp_path / "fake-server.pid").read_text(encoding="utf-8").strip()), 9)
    except (OSError, ValueError):
        pass


def test_test_server_flow_verifies_and_keeps_server_running(tmp_path, monkeypatch, fake_server):
    window = _launch_window(tmp_path, monkeypatch, fake_server)
    assert window.builder.test.text() == "Test Server"
    assert "idle" in window.builder.verify_status.title.text()
    window.builder.command.arguments.append(CommandArgument("--port", [str(_pick_port())]))
    window.builder.rebuild()
    before = window.builder.command.to_dict()
    completed: list = []
    window.verification_service.completed.connect(completed.append)
    window.builder.test.click()
    _spin_until(lambda: completed, timeout=60)
    result = completed[0]
    assert result.verified
    # raw server output reaches the console
    _spin_until(lambda: "server is listening" in window.console.text.toPlainText(), timeout=30)
    # Stop remains available; process keeps running after success
    assert window.builder.stop.isEnabled()
    assert window.runner.running
    # builder command unchanged by staging/probing
    assert window.builder.command.to_dict() == before
    assert window.builder.verify_status.title.text().startswith("<b>Test Server</b> — verified")
    # Stop terminates the server
    _stop_server(window, tmp_path)
    window.close()


def test_test_server_port_substitution_keeps_builder_value(tmp_path, monkeypatch, fake_server):
    window = _launch_window(tmp_path, monkeypatch, fake_server)
    window.builder.command.arguments.append(CommandArgument("--port", ["${PORT}"]))
    window.builder.rebuild()
    completed: list = []
    window.verification_service.completed.connect(completed.append)
    window.builder.test.click()
    _spin_until(lambda: completed, timeout=60)
    assert completed[0].verified
    # ${PORT} remains in the builder; the run used the resolved ephemeral port
    port_row = next(argument for argument in window.builder.command.arguments if argument.flag == "--port")
    assert port_row.values == ["${PORT}"]
    assert window.runner.context is not None and window.runner.context.port != 0
    _stop_server(window, tmp_path)
    window.close()


def test_test_server_timeout_shows_stage_specific_failure(tmp_path, monkeypatch, fake_server):
    monkeypatch.setenv("FAKE_SLOW_503S", "99999")
    window = _launch_window(tmp_path, monkeypatch, fake_server, timeout_s=2)
    window.builder.command.arguments.append(CommandArgument("--port", [str(_pick_port())]))
    window.builder.rebuild()
    completed: list = []
    window.verification_service.completed.connect(completed.append)
    window.builder.test.click()
    _spin_until(lambda: completed, timeout=30)
    result = completed[0]
    assert result.failed_stage == "ready"
    panel = window.builder.verify_status
    assert "failed at" in panel.title.text()
    assert "did not become ready" in panel._detail.text()
    _stop_server(window, tmp_path)
    window.close()


def test_test_server_api_key_never_in_ui(tmp_path, monkeypatch, fake_server):
    from test_server_verification import SENTINEL

    window = _launch_window(tmp_path, monkeypatch, fake_server)
    window.builder.command.arguments.append(CommandArgument("--port", [str(_pick_port())]))
    window.builder.command.arguments.append(CommandArgument("--api-key", [SENTINEL]))
    window.builder.rebuild()
    completed: list = []
    window.verification_service.completed.connect(completed.append)
    window.builder.test.click()
    _spin_until(lambda: completed, timeout=60)
    assert completed[0].verified
    public = "\n".join(
        [
            window.console.text.toPlainText(),
            window.builder.verify_status.title.text(),
            window.builder.verify_status._detail.text(),
            window.statusBar().currentMessage(),
        ]
    )
    assert SENTINEL not in public
    _stop_server(window, tmp_path)
    window.close()


def test_test_server_second_run_resets_panel(tmp_path, monkeypatch, fake_server):
    window = _launch_window(tmp_path, monkeypatch, fake_server)
    window.builder.command.arguments.append(CommandArgument("--port", [str(_pick_port())]))
    window.builder.rebuild()
    completed: list = []
    window.verification_service.completed.connect(completed.append)
    window.builder.test.click()
    _spin_until(lambda: len(completed) == 1, timeout=60)
    assert "verified" in window.builder.verify_status.title.text()
    # Stop the first server before re-testing (fresh port for the new run).
    _stop_server(window, tmp_path)
    port_row = next(argument for argument in window.builder.command.arguments if argument.flag == "--port")
    port_row.values = [str(_pick_port())]
    window.builder.rebuild()
    # A second run resets the panel to pending and verifies again
    window.builder.test.click()
    _spin_until(lambda: len(completed) == 2, timeout=60)
    assert completed[1].verified
    _stop_server(window, tmp_path)
    window.close()
