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
    # Test Command output reaches that console (runner -> console wiring).
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
