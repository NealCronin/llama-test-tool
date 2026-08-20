from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.models.command import Command, CommandArgument
from app.models.flags import FlagSpec
from app.services.flag_catalog import FlagCatalog
from app.services.validation import validate_command
from app.server import SERVER_COMMAND
from app.settings import AppSettings
from app.widgets.argument_row import ArgumentRow
from app.widgets.searchable_flag_picker import SearchableFlagPicker
from app.widgets.server_verify_status import ServerVerifyStatusPanel


class CommandBuilder(QWidget):
    changed = Signal()
    add_to_swap_requested = Signal()
    test_requested = Signal()
    stop_requested = Signal()
    memory_test_requested = Signal()
    memory_options_requested = Signal()
    memory_cancel_requested = Signal()
    benchmark_requested = Signal()
    benchmark_options_requested = Signal()
    benchmark_cancel_requested = Signal()

    def __init__(self, settings: AppSettings, catalog: FlagCatalog, parent=None) -> None:
        super().__init__(parent)
        self.settings, self.catalog = settings, catalog
        self.command = Command.from_dict(settings.last_command) if settings.last_command else Command(executable=SERVER_COMMAND)
        self.command.executable = SERVER_COMMAND
        self.rows: list[ArgumentRow] = []
        self.spacer_rows: list[_SpacerRow] = []
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("<h2>Command Builder</h2>"))
        heading.addStretch()
        self.catalog_status = QLabel(f"Argument catalog: {catalog.source} ({len(catalog.specs)} flags)")
        heading.addWidget(self.catalog_status)
        layout.addLayout(heading)

        self.arguments_host = QWidget()
        self.arguments_layout = QVBoxLayout(self.arguments_host)
        self.arguments_layout.setContentsMargins(0, 0, 0, 0)
        self.arguments_layout.addStretch()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setWidget(self.arguments_host)
        layout.addWidget(scroll, 1)

        button_row = QHBoxLayout()
        add = QPushButton("+ Add Argument")
        add.clicked.connect(self.add_argument)
        self.spacer_button = QPushButton("+ Spacer")
        self.spacer_button.setToolTip("Insert a visual separator between two argument rows. It never changes the command.")
        self.spacer_button.clicked.connect(self.add_spacer)
        button_row.addWidget(add)
        button_row.addWidget(self.spacer_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        controls = QHBoxLayout()
        self.preview_vertical = QCheckBox("Vertical preview")
        self.preview_vertical.setChecked(settings.vertical_preview)
        self.preview_vertical.toggled.connect(self._changed)
        self.copy = QPushButton("Copy Command")
        self.clear = QPushButton("Clear Command")
        self.memory_test = QPushButton("Memory Test")
        self.benchmark = QPushButton("Benchmark")
        self.benchmark_options = QPushButton("Benchmark Options")
        self.benchmark_cancel = QPushButton("Cancel Benchmark")
        self.benchmark_cancel.setEnabled(False)
        self.memory_options = QPushButton("Memory Test Options")
        self.memory_cancel = QPushButton("Cancel Memory Test")
        self.memory_cancel.setEnabled(False)
        self.test = QPushButton("Test Server")
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.add_swap = QPushButton("Add to llama-swap")
        for button in (self.preview_vertical, self.memory_test, self.benchmark, self.benchmark_options, self.benchmark_cancel, self.memory_options, self.memory_cancel, self.test, self.stop, self.copy, self.clear, self.add_swap):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self.verify_status = ServerVerifyStatusPanel(self)
        layout.addWidget(self.verify_status)
        self.preview = QLabel()
        self.preview.setTextInteractionFlags(self.preview.textInteractionFlags() | self.preview.textInteractionFlags().TextSelectableByMouse)
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("background: #111827; color: #e5e7eb; font-family: Consolas, monospace; padding: 10px; border-radius: 4px;")
        layout.addWidget(QLabel("Command Preview"))
        layout.addWidget(self.preview)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)

        self.copy.clicked.connect(self.copy_command)
        self.clear.clicked.connect(self.clear_command)
        self.test.clicked.connect(self.test_requested)
        self.stop.clicked.connect(self.stop_requested)
        self.add_swap.clicked.connect(self.add_to_swap_requested)
        self.memory_test.clicked.connect(self.memory_test_requested)
        self.memory_options.clicked.connect(self.memory_options_requested)
        self.memory_cancel.clicked.connect(self.memory_cancel_requested)
        self.benchmark.clicked.connect(self.benchmark_requested)
        self.benchmark_options.clicked.connect(self.benchmark_options_requested)
        self.benchmark_cancel.clicked.connect(self.benchmark_cancel_requested)
        self.rebuild()

    def set_catalog(self, catalog: FlagCatalog) -> None:
        self.catalog = catalog
        self.catalog_status.setText(f"Argument catalog: {catalog.source} ({len(catalog.specs)} flags)")
        self.rebuild()

    def refresh_folder_for(self, canonical_name: str) -> bool:
        """Re-scan a folder-backed row's options in place."""
        for row in self.rows:
            if row.spec.canonical_name == canonical_name:
                row.refresh_folder()
                return True
        return False

    def rebuild(self) -> None:
        while self.rows:
            row = self.rows.pop()
            self.arguments_layout.removeWidget(row)
            row.deleteLater()
        while self.spacer_rows:
            spacer = self.spacer_rows.pop()
            self.arguments_layout.removeWidget(spacer)
            spacer.deleteLater()
        spacers = {boundary for boundary in self.settings.builder_spacers if 1 <= boundary <= len(self.command.arguments) - 1}
        for position, argument in enumerate(self.command.arguments, start=1):
            spec = self.catalog.find(argument.flag)
            if spec is None:
                spec = FlagSpec(argument.flag, (argument.flag,), "Unknown argument retained from saved/imported command.", len(argument.values))
            row = ArgumentRow(argument, spec, self.settings, removable=argument.flag not in {"-m", "--model"}, parent=self.arguments_host)
            row.changed.connect(self._changed)
            row.remove_requested.connect(self.remove_row)
            row.move_requested.connect(self.move_row)
            self.arguments_layout.insertWidget(self.arguments_layout.count() - 1, row)
            self.rows.append(row)
            if position in spacers:
                spacer = _SpacerRow(position, len(self.command.arguments) - 1, self.move_spacer, self.remove_spacer)
                self.arguments_layout.insertWidget(self.arguments_layout.count() - 1, spacer)
                self.spacer_rows.append(spacer)
        self.spacer_button.setEnabled(len(self.command.arguments) >= 2)
        self._changed()

    def add_argument(self) -> None:
        picker = SearchableFlagPicker(self.catalog, self.settings.pinned_flags, self)
        picker.exec()
        if picker.pinned_flags != self.settings.pinned_flags:
            self.settings.pinned_flags = picker.pinned_flags
            self._changed()
        if picker.selected:
            self.add_spec(picker.selected, flag=picker.selected_flag)

    def add_spec(self, spec: FlagSpec, values: list[str] | None = None, source_type: str = "manual", flag: str | None = None) -> None:
        if spec.canonical_name == "--model":
            return
        if spec.canonical_name in {"--chat-template", "--chat-template-file"} and values and values[0]:
            self._ensure_jinja_before_template()
        initial_values = values if values is not None else ([] if spec.optional_parameter else [""] * spec.parameter_count)
        self.command.arguments.append(CommandArgument(flag or spec.preferred_name, initial_values, source_type))
        self.rebuild()

    def set_argument(self, canonical_name: str, values: list[str], source_type: str = "preset") -> None:
        spec = self.catalog.find(canonical_name)
        if spec is None:
            return
        if spec.canonical_name in {"--chat-template", "--chat-template-file"} and values and values[0]:
            self._ensure_jinja_before_template()
        existing = next((argument for argument in self.command.arguments if (current := self.catalog.find(argument.flag)) and current.canonical_name == spec.canonical_name), None)
        if existing is None:
            self.command.arguments.append(CommandArgument(spec.preferred_name, list(values), source_type))
        else:
            existing.values, existing.source_type = list(values), source_type
        self.rebuild()

    def _ensure_jinja_before_template(self) -> None:
        if self.catalog.find("--jinja") is None:
            return
        self.command.arguments = [argument for argument in self.command.arguments if not ((current := self.catalog.find(argument.flag)) and current.canonical_name == "--jinja")]
        template_index = next((i for i, arg in enumerate(self.command.arguments) if (current := self.catalog.find(arg.flag)) and current.canonical_name in {"--chat-template", "--chat-template-file"}), len(self.command.arguments))
        self.command.arguments.insert(template_index, CommandArgument("--jinja"))

    def apply_preset(self, preset: str) -> None:
        lookup = {
            "mmproj": ("--mmproj", [""], "mmproj"),
            "mtp": ("--spec-type", ["draft-mtp"], "preset"),
            "ngram": ("--spec-type", ["ngram-mod"], "preset"),
            "template": ("--chat-template-file", [""], "template_file"),
        }
        name, values, source = lookup[preset]
        self.set_argument(name, values, source_type=source)

    def remove_row(self, row: ArgumentRow) -> None:
        self.command.arguments.remove(row.argument)
        self.rebuild()

    def move_row(self, row: ArgumentRow, offset: int) -> None:
        index = self.command.arguments.index(row.argument)
        target = index + offset
        if row.argument.flag in {"-m", "--model"} or target < 1 or target >= len(self.command.arguments):
            return
        self.command.arguments[index], self.command.arguments[target] = self.command.arguments[target], self.command.arguments[index]
        self.rebuild()

    def add_spacer(self) -> None:
        spacers = set(self.settings.builder_spacers)
        for boundary in range(len(self.command.arguments) - 1, 0, -1):
            if boundary not in spacers:
                spacers.add(boundary)
                self.settings.builder_spacers = sorted(spacers)
                self.rebuild()
                return

    def move_spacer(self, boundary: int, offset: int) -> None:
        target = boundary + offset
        if not 1 <= target <= len(self.command.arguments) - 1:
            return
        spacers = set(self.settings.builder_spacers)
        if target in spacers:
            return
        spacers.discard(boundary)
        spacers.add(target)
        self.settings.builder_spacers = sorted(spacers)
        self.rebuild()

    def remove_spacer(self, boundary: int) -> None:
        if boundary not in self.settings.builder_spacers:
            return
        spacers = set(self.settings.builder_spacers)
        spacers.discard(boundary)
        self.settings.builder_spacers = sorted(spacers)
        self.rebuild()

    def load_command(self, command: Command) -> None:
        """Replace the visible command (imported from llama-swap); resets the spacer layout."""
        self.command = command
        self.settings.builder_spacers = []
        self.rebuild()

    def clear_command(self) -> None:
        if QMessageBox.question(self, "Clear command", "Remove every argument except the model selector?") != QMessageBox.StandardButton.Yes:
            return
        self.command.reset()
        self.settings.builder_spacers = []
        self.rebuild()

    def set_running(self, running: bool) -> None:
        self.test.setEnabled(not running)
        self.stop.setEnabled(running)

    def set_memory_running(self, running: bool) -> None:
        self.memory_test.setEnabled(not running)
        self.memory_options.setEnabled(not running)
        self.memory_cancel.setEnabled(running)


    def set_benchmark_running(self, running: bool) -> None:
        self.benchmark.setEnabled(not running)
        self.benchmark_options.setEnabled(not running)
        self.benchmark_cancel.setEnabled(running)
    def copy_command(self) -> None:
        QGuiApplication.clipboard().setText(self.command.rendered(vertical=self.preview_vertical.isChecked()))

    def _changed(self, *_args) -> None:
        self.settings.vertical_preview = self.preview_vertical.isChecked()
        self.command.ensure_model_argument()
        self.preview.setText(self.command.rendered(vertical=self.preview_vertical.isChecked()))
        issues = validate_command(self.command, self.catalog)
        if issues:
            rendered = "<br>".join(f"<b>{issue.severity.title()}:</b> {issue.message}" for issue in issues)
            self.validation.setText(rendered)
            self.validation.setStyleSheet("color: #b45309;" if all(issue.severity == "warning" for issue in issues) else "color: #b91c1c;")
        else:
            self.validation.setText("Ready to test or add to llama-swap.")
            self.validation.setStyleSheet("color: #15803d;")
        self.changed.emit()


class _SpacerRow(QWidget):
    """Presentation-only separator between two argument rows; never part of the command."""

    def __init__(self, boundary: int, max_boundary: int, on_move, on_remove) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(2)
        controls.addStretch()
        up = QPushButton("↑", self)
        down = QPushButton("↓", self)
        remove = QPushButton("×", self)
        up.setToolTip("Move spacer up")
        down.setToolTip("Move spacer down")
        remove.setToolTip("Remove spacer")
        up.setEnabled(boundary > 1)
        down.setEnabled(boundary < max_boundary)
        up.clicked.connect(lambda: on_move(boundary, -1))
        down.clicked.connect(lambda: on_move(boundary, 1))
        remove.clicked.connect(lambda: on_remove(boundary))
        for button in (up, down, remove):
            button.setFixedHeight(20)
            button.setMinimumWidth(24)
            controls.addWidget(button)
        layout.addLayout(controls)
