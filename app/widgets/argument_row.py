from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QWidget,
)

from app.models.command import CommandArgument
from app.models.flags import FlagSpec
from app.services.file_scanner import scan_gguf_models, scan_templates
from app.settings import AppSettings


class SpecTypeCombo(QComboBox):
    """Checkbox picker that renders llama.cpp's comma-separated spec-type syntax."""

    def __init__(self, choices: tuple[str, ...], value: str, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.setModel(QStandardItemModel(self))
        for choice in choices:
            item = QStandardItem(choice)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
            self.model().appendRow(item)
        self.view().pressed.connect(self._toggle)
        self.set_values(value)

    def _toggle(self, index) -> None:
        item = self.model().itemFromIndex(index)
        item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)
        self._sync_text()

    def set_values(self, value: str) -> None:
        selected = set(filter(None, value.split(",")))
        for row in range(self.model().rowCount()):
            item = self.model().item(row)
            item.setCheckState(Qt.CheckState.Checked if item.text() in selected else Qt.CheckState.Unchecked)
        self._sync_text()

    def _sync_text(self) -> None:
        values = [self.model().item(row).text() for row in range(self.model().rowCount()) if self.model().item(row).checkState() == Qt.CheckState.Checked]
        self.setEditText(",".join(values))


class ArgumentRow(QWidget):
    changed = Signal()
    remove_requested = Signal(object)
    move_requested = Signal(object, int)

    def __init__(self, argument: CommandArgument, spec: FlagSpec, settings: AppSettings, removable: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.argument, self.spec, self.settings = argument, spec, settings
        self.value_widgets: list[QWidget] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self.flag_label = QLabel(argument.flag)
        self.flag_label.setMinimumWidth(185)
        self.flag_label.setToolTip(self.detail_text())
        layout.addWidget(self.flag_label)
        self._build_values(layout)
        self.up = QPushButton("↑", toolTip="Move argument earlier")
        self.down = QPushButton("↓", toolTip="Move argument later")
        layout.addWidget(self.up)
        layout.addWidget(self.down)
        self.up.clicked.connect(lambda: self.move_requested.emit(self, -1))
        self.down.clicked.connect(lambda: self.move_requested.emit(self, 1))
        if removable:
            remove = QPushButton("×", toolTip="Remove argument")
            remove.setAccessibleName(f"Remove {argument.flag}")
            remove.clicked.connect(lambda: self.remove_requested.emit(self))
            layout.addWidget(remove)
        else:
            self.up.setEnabled(False)
            self.down.setEnabled(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def detail_text(self) -> str:
        params = " ".join(self.spec.parameter_names)
        return f"{', '.join(self.spec.aliases)} {params}\n\n{self.spec.description}".strip()

    def _build_values(self, layout: QHBoxLayout) -> None:
        special = self.spec.special_editor
        if special == "model":
            self._folder_combo(layout, self.settings.models_folder, scan_gguf_models, "Select model…", "model")
        elif special == "mmproj":
            self._folder_combo(layout, self.settings.mmproj_folder, scan_gguf_models, "Select MMProj…", "mmproj")
        elif special == "template_file":
            self._folder_combo(layout, self.settings.template_folder, scan_templates, "Select template…", "template")
        elif special == "draft_model":
            self._draft_editor(layout)
        elif special == "spec_type":
            self._spec_type_editor(layout)
        elif special == "chat_template":
            self._template_editor(layout)
        elif self.spec.parameter_count:
            for index in range(self.spec.parameter_count):
                value = self.argument.values[index] if index < len(self.argument.values) else ""
                if self.spec.choices:
                    widget = QComboBox()
                    widget.setEditable(True)
                    widget.addItems(self.spec.choices)
                    widget.setCurrentText(value)
                    widget.currentTextChanged.connect(self._sync_values)
                else:
                    widget = QLineEdit(value)
                    widget.setPlaceholderText(self.spec.parameter_names[index] if index < len(self.spec.parameter_names) else "Value")
                    widget.textChanged.connect(self._sync_values)
                layout.addWidget(widget, 1)
                self.value_widgets.append(widget)
        else:
            layout.addWidget(QLabel("Enabled"))

    def _folder_combo(self, layout: QHBoxLayout, folder: str, scanner, placeholder: str, source: str) -> None:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(40)
        combo.addItem(placeholder, "")
        for path in scanner(folder):
            combo.addItem(path.name, str(path))
        current = self.argument.values[0] if self.argument.values else ""
        found = combo.findData(current)
        if found >= 0:
            combo.setCurrentIndex(found)
        elif current:
            combo.addItem(f"⚠ Missing: {Path(current).name}", current)
            combo.setCurrentIndex(combo.count() - 1)
        combo.currentIndexChanged.connect(self._sync_folder_value)
        layout.addWidget(combo, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse_file(combo, source))
        refresh = QPushButton("↻", toolTip="Refresh folder files")
        refresh.clicked.connect(lambda: self._refresh_folder_combo(combo, folder, scanner, placeholder))
        layout.addWidget(browse)
        layout.addWidget(refresh)
        self.value_widgets.append(combo)

    def _draft_editor(self, layout: QHBoxLayout) -> None:
        self._folder_combo(layout, self.settings.drafters_folder, scan_gguf_models, "Select drafter…", "draft_model")

    def _template_editor(self, layout: QHBoxLayout) -> None:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem("Model default / metadata", "__default__")
        for choice in self.spec.choices:
            combo.addItem(choice, choice)
        combo.addItem("Custom value…", "__custom__")
        value = self.argument.values[0] if self.argument.values else ""
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else (0 if not value else -1))
        if not value:
            self.argument.source_type = "model_default_template"
        if index < 0 and value:
            combo.setEditText(value)
        combo.currentTextChanged.connect(lambda _: self._sync_template_value(combo))
        layout.addWidget(combo, 1)
        self.value_widgets.append(combo)

    def _spec_type_editor(self, layout: QHBoxLayout) -> None:
        combo = SpecTypeCombo(self.spec.choices, self.argument.values[0] if self.argument.values else "")
        combo.setToolTip("Select one or more documented speculative decoding types.")
        combo.currentTextChanged.connect(self._sync_values)
        layout.addWidget(combo, 1)
        self.value_widgets.append(combo)

    def _browse_file(self, combo: QComboBox, source: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose file", combo.currentData() or "", "All files (*)")
        if not path:
            return
        index = combo.findData(path)
        if index < 0:
            combo.addItem(Path(path).name, path)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        self.argument.source_type = source
        self._sync_folder_value()

    def _refresh_folder_combo(self, combo: QComboBox, folder: str, scanner, placeholder: str) -> None:
        current = combo.currentData() or ""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(placeholder, "")
        for path in scanner(folder):
            combo.addItem(path.name, str(path))
        index = combo.findData(current)
        if current and index < 0:
            combo.addItem(f"⚠ Missing: {Path(current).name}", current)
            index = combo.count() - 1
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)
        self._sync_folder_value()

    def refresh_folder(self) -> None:
        """Re-scan this row's folder combo from current settings (post-download refresh)."""
        special = self.spec.special_editor
        if special not in ("model", "mmproj", "template_file", "draft_model"):
            return
        folder = {
            "model": self.settings.models_folder,
            "mmproj": self.settings.mmproj_folder,
            "template_file": self.settings.template_folder,
            "draft_model": self.settings.drafters_folder,
        }[special]
        scanner = scan_templates if special == "template_file" else scan_gguf_models
        placeholder = {"model": "Select model…", "mmproj": "Select MMProj…", "template_file": "Select template…", "draft_model": "Select drafter…"}[special]
        self._refresh_folder_combo(self.value_widgets[0], folder, scanner, placeholder)

    def _sync_template_value(self, combo: QComboBox) -> None:
        selected = combo.currentData()
        if selected == "__default__":
            self.argument.values = [""]
            self.argument.source_type = "model_default_template"
        elif selected == "__custom__":
            combo.setCurrentIndex(-1)
            combo.clearEditText()
            combo.lineEdit().setFocus()
            return
        else:
            self.argument.values = [combo.currentText()]
            self.argument.source_type = "manual"
        self.changed.emit()

    def _sync_folder_value(self, *_args) -> None:
        combo = self.value_widgets[0]
        self.argument.values = [combo.currentData() or combo.currentText()]
        self.changed.emit()


    def _sync_values(self, *_args) -> None:
        values: list[str] = []
        for widget in self.value_widgets:
            if isinstance(widget, QLineEdit):
                values.append(widget.text())
            elif isinstance(widget, QComboBox):
                values.append(widget.currentText())
        self.argument.values = [] if self.spec.optional_parameter and values == [""] else values[:self.spec.parameter_count]
        self.changed.emit()
