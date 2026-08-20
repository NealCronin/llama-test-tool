"""Command Builder UX: pinned picker arguments, visual spacers, and help-block removal."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from app.models.command import Command
from app.services.flag_catalog import FlagCatalog
from app.server import SERVER_COMMAND
from app.settings import AppSettings
from app.widgets.argument_row import ArgumentRow
from app.widgets.command_builder import CommandBuilder, _SpacerRow
from app.widgets.searchable_flag_picker import SearchableFlagPicker


CATALOG_MARKDOWN = """\
| `-m, --model PATH` | model |
| `-c, --ctx-size N` | context |
| `-fa, --flash-attn [on|off|auto]` | Flash Attention |
| `--perf, --no-perf` | performance timing |
| `-op, --port PORT` | server port |
"""


def catalog() -> FlagCatalog:
    return FlagCatalog.parse_readme(CATALOG_MARKDOWN)


def builder(settings: AppSettings | None = None) -> CommandBuilder:
    QApplication.instance() or QApplication([])
    return CommandBuilder(settings or AppSettings(), catalog())


def item_for(picker: SearchableFlagPicker, flag: str):
    for index in range(picker.results.count()):
        item = picker.results.item(index)
        if item.data(Qt.ItemDataRole.UserRole + 1) == flag:
            return item
    raise AssertionError(f"picker has no entry for {flag}")


def picker_flags(picker: SearchableFlagPicker) -> list[str]:
    return [picker.results.item(index).data(Qt.ItemDataRole.UserRole + 1) for index in range(picker.results.count())]


def three_arg_widget(settings: AppSettings | None = None) -> tuple[AppSettings, CommandBuilder]:
    settings = settings or AppSettings()
    widget = builder(settings)
    widget.set_argument("--ctx-size", ["4096"])
    widget.set_argument("--flash-attn", ["on"])
    assert [argument.flag for argument in widget.command.arguments] == ["-m", "-c", "-fa"]
    return settings, widget


def test_picker_lists_every_catalog_flag_without_advanced_filter():
    picker = SearchableFlagPicker(catalog())
    assert not hasattr(picker, "advanced")
    assert set(picker_flags(picker)) == {"-m", "-c", "-fa", "--perf", "--no-perf", "--port"}


def test_pinned_flags_sort_first_and_follow_pin_order():
    first = SearchableFlagPicker(catalog(), ["--ctx-size", "--port"])
    second = SearchableFlagPicker(catalog(), ["--port", "--ctx-size"])
    assert picker_flags(first)[:2] == ["-c", "--port"]
    assert picker_flags(second)[:2] == ["--port", "-c"]
    assert set(picker_flags(first)) == set(picker_flags(second))


def test_star_pins_flag_moves_it_first_and_unpins():
    picker = SearchableFlagPicker(catalog())
    row = picker.results.itemWidget(item_for(picker, "-fa"))
    assert row.star.text() == "☆"
    row.star.click()
    assert picker.pinned_flags == ["--flash-attn"]
    assert picker_flags(picker)[0] == "-fa"
    assert picker.results.itemWidget(picker.results.item(0)).star.text() == "★"
    picker.results.itemWidget(picker.results.item(0)).star.click()
    assert picker.pinned_flags == []
    assert picker_flags(picker)[0] != "-fa"


def test_star_click_never_selects_or_adds():
    picker = SearchableFlagPicker(catalog())
    before = picker.results.currentItem().data(Qt.ItemDataRole.UserRole + 1)
    picker.results.itemWidget(item_for(picker, "-fa")).star.click()
    assert picker.selected is None
    assert picker.results.currentItem().data(Qt.ItemDataRole.UserRole + 1) == before


def test_double_click_selects_negative_boolean_variant():
    picker = SearchableFlagPicker(catalog())
    picker.results.itemDoubleClicked.emit(item_for(picker, "--no-perf"))
    assert picker.selected is not None
    assert picker.selected_flag == "--no-perf"
    assert picker.selected.canonical_name == "--perf"


def test_picker_rows_show_flag_descriptions():
    picker = SearchableFlagPicker(catalog())
    label = picker.results.itemWidget(item_for(picker, "-c")).findChild(QLabel)
    assert "context" in label.text()
    assert label.toolTip()
    for index in range(picker.results.count()):
        assert picker.results.itemWidget(picker.results.item(index)).findChild(QLabel).text()


def test_builder_persists_pin_changes_even_when_picker_cancels(monkeypatch):
    settings = AppSettings()
    widget = builder(settings)

    def fake_exec(self):
        self._toggle_pin("--flash-attn", True)
        return False

    monkeypatch.setattr(SearchableFlagPicker, "exec", fake_exec)
    widget.add_argument()
    assert settings.pinned_flags == ["--flash-attn"]
    assert [argument.flag for argument in widget.command.arguments] == ["-m"]


def test_builder_adds_argument_double_clicked_in_picker(monkeypatch):
    settings = AppSettings()
    widget = builder(settings)

    def fake_exec(self):
        item = item_for(self, "-c")
        self.results.setCurrentItem(item)
        self.results.itemDoubleClicked.emit(item)
        return True

    monkeypatch.setattr(SearchableFlagPicker, "exec", fake_exec)
    widget.add_argument()
    assert [argument.flag for argument in widget.command.arguments] == ["-m", "-c"]
    assert settings.pinned_flags == []


def test_builder_has_no_permanent_command_description_block():
    widget = builder()
    assert not hasattr(widget, "help_label")


def test_argument_rows_keep_description_tooltips():
    widget = builder()
    widget.set_argument("--ctx-size", ["4096"])
    row = widget.rows[1]
    assert row.flag_label.toolTip() == row.detail_text()
    assert "context" in row.flag_label.toolTip()


def test_spacers_never_change_argv_rendering_or_validation():
    settings, widget = three_arg_widget()
    base_argv = widget.command.argv()
    base_flat = widget.command.rendered()
    base_vertical = widget.command.rendered(vertical=True)
    base_lines = widget.command.rendered_lines()
    base_validation = widget.validation.text()
    widget.add_spacer()
    widget.add_spacer()
    widget.move_spacer(2, -1)
    assert settings.builder_spacers == [1, 2]
    assert widget.command.argv() == base_argv
    assert widget.command.rendered() == base_flat
    assert widget.command.rendered(vertical=True) == base_vertical
    assert widget.command.rendered_lines() == base_lines
    assert widget.validation.text() == base_validation


def test_spacers_are_not_command_arguments():
    settings, widget = three_arg_widget()
    widget.add_spacer()
    assert len(widget.command.arguments) == 3
    assert all(isinstance(row, ArgumentRow) for row in widget.rows)
    assert all(isinstance(spacer, _SpacerRow) for spacer in widget.spacer_rows)


def test_add_spacer_defaults_to_last_free_boundary_and_saturates():
    settings, widget = three_arg_widget()
    widget.add_spacer()
    assert settings.builder_spacers == [2]
    widget.add_spacer()
    assert settings.builder_spacers == [1, 2]
    widget.add_spacer()
    assert settings.builder_spacers == [1, 2]


def test_spacer_button_click_adds_spacer():
    settings, widget = three_arg_widget()
    widget.spacer_button.click()
    assert settings.builder_spacers == [2]


def test_move_spacer_clamps_and_refuses_collisions():
    settings, widget = three_arg_widget()
    widget.add_spacer()
    widget.move_spacer(2, 1)
    assert settings.builder_spacers == [2]
    widget.add_spacer()
    widget.move_spacer(1, 1)
    assert settings.builder_spacers == [1, 2]
    widget.move_spacer(1, -1)
    assert settings.builder_spacers == [1, 2]
    widget.remove_spacer(2)
    assert settings.builder_spacers == [1]
    widget.move_spacer(1, 1)
    assert settings.builder_spacers == [2]


def test_spacer_rows_interleave_and_model_stays_first():
    settings, widget = three_arg_widget()
    settings.builder_spacers = [1, 2]
    widget.rebuild()
    children = [widget.arguments_layout.itemAt(index).widget() for index in range(widget.arguments_layout.count())]
    children = [child for child in children if child is not None]
    assert [type(child).__name__ for child in children] == [
        "ArgumentRow", "_SpacerRow", "ArgumentRow", "_SpacerRow", "ArgumentRow",
    ]
    assert widget.rows[0].argument.flag == "-m"
    assert len(widget.spacer_rows) == 2


def test_spacers_restore_for_saved_command():
    settings, widget = three_arg_widget()
    widget.add_spacer()
    settings.last_command = widget.command.to_dict()
    restored = builder(settings)
    assert [argument.flag for argument in restored.command.arguments] == ["-m", "-c", "-fa"]
    assert len(restored.spacer_rows) == 1


def test_out_of_range_spacer_values_are_ignored():
    settings = AppSettings(builder_spacers=[-1, 0, 99, 1])
    widget = builder(settings)
    widget.set_argument("--ctx-size", ["4096"])
    widget.set_argument("--flash-attn", ["on"])
    widget.rebuild()
    assert len(widget.spacer_rows) == 1
    assert [argument.flag for argument in widget.command.arguments] == ["-m", "-c", "-fa"]


def test_spacer_button_requires_two_arguments():
    widget = builder()
    assert not widget.spacer_button.isEnabled()
    widget.set_argument("--ctx-size", ["4096"])
    assert widget.spacer_button.isEnabled()


def test_remove_row_drops_spacer_that_left_no_valid_boundary():
    settings, widget = three_arg_widget()
    widget.add_spacer()
    widget.remove_row(widget.rows[1])
    assert [argument.flag for argument in widget.command.arguments] == ["-m", "-fa"]
    assert len(widget.spacer_rows) == 0
    assert widget.command.argv() == [SERVER_COMMAND, "-m", "", "-fa", "on"]


def test_clear_command_removes_spacers_but_keeps_pins(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes))
    settings, widget = three_arg_widget()
    settings.pinned_flags = ["--port"]
    widget.add_spacer()
    widget.clear_command()
    assert settings.builder_spacers == []
    assert settings.pinned_flags == ["--port"]
    assert [argument.flag for argument in widget.command.arguments] == ["-m"]
    assert len(widget.spacer_rows) == 0


def test_load_command_resets_spacers():
    settings, widget = three_arg_widget()
    widget.add_spacer()
    widget.load_command(Command.from_dict({
        "executable": "llama-server",
        "arguments": [
            {"flag": "-m", "values": ["model.gguf"]},
            {"flag": "-c", "values": ["1024"]},
        ],
    }))
    assert settings.builder_spacers == []
    assert len(widget.spacer_rows) == 0
    assert [argument.flag for argument in widget.command.arguments] == ["-m", "-c"]


def test_presets_do_not_change_spacers():
    settings, widget = three_arg_widget()
    widget.add_spacer()
    widget.apply_preset("mtp")
    assert settings.builder_spacers == [2]
    assert len(widget.spacer_rows) == 1


def test_settings_roundtrip_normalizes_pins_spacers_and_legacy_keys(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "pinned_flags": ["--flash-attn", "--flash-attn", 7, "--port"],
        "builder_spacers": [2, 1, 2, True, "x"],
        "picker_show_advanced": True,
    }), encoding="utf-8")
    monkeypatch.setattr(AppSettings, "path", classmethod(lambda cls: target))
    settings = AppSettings.load()
    assert settings.pinned_flags == ["--flash-attn", "--port"]
    assert settings.builder_spacers == [2, 1]
    assert not hasattr(settings, "picker_show_advanced")
    settings.save()
    reloaded = json.loads(target.read_text(encoding="utf-8"))
    assert reloaded["pinned_flags"] == ["--flash-attn", "--port"]
    assert reloaded["builder_spacers"] == [2, 1]
    assert "picker_show_advanced" not in reloaded
