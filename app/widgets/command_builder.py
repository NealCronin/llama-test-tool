from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app.models.command import Command, CommandArgument
from app.models.flags import FlagSpec
from app.services.flag_catalog import FlagCatalog
from app.services.validation import validate_command
from app.settings import AppSettings
from app.widgets.argument_row import ArgumentRow
from app.widgets.searchable_flag_picker import SearchableFlagPicker


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
        self.command = Command.from_dict(settings.last_command) if settings.last_command else Command()
        self.command.executable = "llama-server"
        self.rows: list[ArgumentRow] = []
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

        add = QPushButton("+ Add Argument")
        add.clicked.connect(self.add_argument)
        layout.addWidget(add)
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("color: #6b7280; padding: 4px;")
        layout.addWidget(self.help_label)

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
        self.test = QPushButton("Test Command")
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.add_swap = QPushButton("Add to llama-swap")
        for button in (self.preview_vertical, self.memory_test, self.benchmark, self.benchmark_options, self.benchmark_cancel, self.memory_options, self.memory_cancel, self.test, self.stop, self.copy, self.clear, self.add_swap):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
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

    def rebuild(self) -> None:
        while self.rows:
            row = self.rows.pop()
            self.arguments_layout.removeWidget(row)
            row.deleteLater()
        for argument in self.command.arguments:
            spec = self.catalog.find(argument.flag)
            if spec is None:
                spec = FlagSpec(argument.flag, (argument.flag,), "Unknown argument retained from saved/imported command.", len(argument.values))
            row = ArgumentRow(argument, spec, self.settings, removable=argument.flag not in {"-m", "--model"}, parent=self.arguments_host)
            row.changed.connect(self._changed)
            row.remove_requested.connect(self.remove_row)
            row.move_requested.connect(self.move_row)
            row.flag_label.enterEvent = lambda event, text=row.detail_text(): self.help_label.setText(text)
            self.arguments_layout.insertWidget(self.arguments_layout.count() - 1, row)
            self.rows.append(row)
        self._changed()

    def add_argument(self) -> None:
        picker = SearchableFlagPicker(self.catalog, self)
        if picker.exec() and picker.selected:
            self.add_spec(picker.selected, flag=picker.selected_flag)

    def add_spec(self, spec: FlagSpec, values: list[str] | None = None, source_type: str = "manual", flag: str | None = None) -> None:
        if spec.canonical_name == "--model":
            return
        if spec.canonical_name in {"--chat-template", "--chat-template-file"}:
            self._ensure_jinja_before_template()
        initial_values = values if values is not None else ([] if spec.optional_parameter else [""] * spec.parameter_count)
        self.command.arguments.append(CommandArgument(flag or spec.preferred_name, initial_values, source_type))
        self.rebuild()
    def _ensure_jinja_before_template(self) -> None:
        if self.command.has_flag({"--jinja"}):
            return
        jinja = self.catalog.find("--jinja")
        if jinja:
            template_index = next((i for i, arg in enumerate(self.command.arguments) if self.catalog.find(arg.flag) and self.catalog.find(arg.flag).canonical_name in {"--chat-template", "--chat-template-file"}), len(self.command.arguments))
            self.command.arguments.insert(template_index, CommandArgument("--jinja"))

    def apply_preset(self, preset: str) -> None:
        if preset in {"dflash", "dspark"}:
            draft = self.catalog.find("--spec-draft-model")
            if draft and not any(self.catalog.find(argument.flag) and self.catalog.find(argument.flag).canonical_name == "--spec-draft-model" for argument in self.command.arguments):
                source = "dflash" if preset == "dflash" else "dspark"
                self.command.arguments.append(CommandArgument(draft.preferred_name, [""], "draft_model", {"draft_source": source}))
            spec = self.catalog.find("--spec-type")
            if spec:
                self.command.arguments.append(CommandArgument(spec.preferred_name, [f"draft-{preset}"], "preset"))
            self.rebuild()
            return
        lookup = {
            "mmproj": ("--mmproj", [""], "mmproj"),
            "mtp": ("--spec-type", ["draft-mtp"], "preset"),
            "ngram": ("--spec-type", ["ngram-mod"], "preset"),
            "template": ("--chat-template-file", [""], "template_file"),
        }
        name, values, source = lookup[preset]
        spec = self.catalog.find(name)
        if spec:
            self.add_spec(spec, values, source)

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

    def clear_command(self) -> None:
        if QMessageBox.question(self, "Clear command", "Remove every argument except the model selector?") != QMessageBox.StandardButton.Yes:
            return
        self.command.reset()
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
        QGuiApplication.clipboard().setText(self.command.rendered(self.settings.llama_server_executable or None, vertical=self.preview_vertical.isChecked()))

    def _changed(self, *_args) -> None:
        self.settings.vertical_preview = self.preview_vertical.isChecked()
        self.command.ensure_model_argument()
        self.preview.setText(self.command.rendered(self.settings.llama_server_executable or None, vertical=self.preview_vertical.isChecked()))
        issues = validate_command(self.command, self.catalog)
        if issues:
            rendered = "<br>".join(f"<b>{issue.severity.title()}:</b> {issue.message}" for issue in issues)
            self.validation.setText(rendered)
            self.validation.setStyleSheet("color: #b45309;" if all(issue.severity == "warning" for issue in issues) else "color: #b91c1c;")
        else:
            self.validation.setText("Ready to test or add to llama-swap.")
            self.validation.setStyleSheet("color: #15803d;")
        self.changed.emit()
