from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from app.services.llama_swap_service import LlamaSwapError, LlamaSwapService


class ConfigViewer(QWidget):
    load_requested = Signal(str)
    status = Signal(str)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.current_id: str | None = None
        layout = QVBoxLayout(self)
        globals_box = QGroupBox("Global Settings")
        globals_form = QFormLayout(globals_box)
        self.global_numbers = {key: QLineEdit() for key in ("healthCheckTimeout", "globalTTL", "unloadTimeout", "startPort")}
        self.log_level = QComboBox(); self.log_level.addItems(("debug", "info", "warn", "error"))
        self.log_output = QComboBox(); self.log_output.addItems(("proxy", "upstream", "both", "none"))
        self.include_aliases = QCheckBox("Include aliases in model list")
        globals_form.addRow("Health check timeout", self.global_numbers["healthCheckTimeout"])
        globals_form.addRow("Global TTL", self.global_numbers["globalTTL"])
        globals_form.addRow("Unload timeout", self.global_numbers["unloadTimeout"])
        globals_form.addRow("Log level", self.log_level)
        globals_form.addRow("Log output", self.log_output)
        globals_form.addRow(self.include_aliases)
        globals_form.addRow("Start port", self.global_numbers["startPort"])
        global_buttons = QHBoxLayout()
        self.save_globals = QPushButton("Save Global Settings")
        self.inherit_timeouts = QPushButton("Make model TTL/unload inherit globals")
        global_buttons.addWidget(self.save_globals); global_buttons.addWidget(self.inherit_timeouts)
        globals_form.addRow(global_buttons)
        metadata = QGroupBox("Model Settings")
        metadata_form = QFormLayout(metadata)
        self.ttl_mode = QComboBox(); self.ttl_mode.addItems(("Inherit global", "Override"))
        self.ttl_value = QLineEdit(); self.ttl_value.setEnabled(False)
        self.unload_mode = QComboBox(); self.unload_mode.addItems(("Inherit global", "Override"))
        self.unload_value = QLineEdit(); self.unload_value.setEnabled(False)
        self.save_model_settings = QPushButton("Save Model Settings")
        self.ttl_mode.currentTextChanged.connect(lambda mode: self.ttl_value.setEnabled(mode == "Override"))
        self.unload_mode.currentTextChanged.connect(lambda mode: self.unload_value.setEnabled(mode == "Override"))
        metadata_form.addRow("TTL", self.ttl_mode)
        metadata_form.addRow("TTL override", self.ttl_value)
        metadata_form.addRow("Unload timeout", self.unload_mode)
        metadata_form.addRow("Unload override", self.unload_value)
        metadata_form.addRow(self.save_model_settings)
        layout.addWidget(globals_box)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search llama-swap models…")
        refresh = QPushButton("Refresh")
        top.addWidget(self.search)
        top.addWidget(refresh)
        layout.addLayout(top)
        splitter = QSplitter()
        self.models = QListWidget()
        self.details = QLabel("Choose a llama-swap model.")
        self.details.setWordWrap(True)
        self.raw = QTextEdit()
        self.raw.setPlaceholderText("Raw Command Mode — commands with shell syntax are preserved here.")
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.details)
        right_layout.addWidget(QLabel("Command"))
        right_layout.addWidget(self.raw, 1)
        right_layout.addWidget(metadata)
        buttons = QHBoxLayout()
        self.load = QPushButton("Load into Command Builder")
        self.save = QPushButton("Save Changes")
        self.duplicate = QPushButton("Duplicate")
        self.remove = QPushButton("Remove Model")
        for button in (self.load, self.save, self.duplicate, self.remove):
            buttons.addWidget(button)
        right_layout.addLayout(buttons)
        splitter.addWidget(self.models)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.search.textChanged.connect(self.refresh)
        refresh.clicked.connect(self.refresh)
        self.models.currentItemChanged.connect(self._select)
        self.load.clicked.connect(self._load)
        self.save.clicked.connect(self._save)
        self.duplicate.clicked.connect(self._duplicate)
        self.remove.clicked.connect(self._remove)
        self.save_globals.clicked.connect(self._save_globals)
        self.save_model_settings.clicked.connect(self._save_model_settings)
        self.inherit_timeouts.clicked.connect(self._inherit_timeouts)
        self.refresh()

    def service(self) -> LlamaSwapService | None:
        return LlamaSwapService(self.settings.llama_swap_config, self.settings.backup_limit) if self.settings.llama_swap_config else None

    def refresh(self) -> None:
        selected = self.current_id
        self.models.clear()
        self.current_id = None
        service = self.service()
        if service is None:
            self.details.setText("Choose a llama-swap configuration in Settings.")
            return
        try:
            data = service.load()
            entries = data["models"]
        except LlamaSwapError as error:
            self.details.setText(str(error))
            return
        self._load_globals(data)
        query = self.search.text().casefold()
        for model_id, entry in entries.items():
            name = entry.get("name", "") if isinstance(entry, dict) else ""
            command = entry.get("cmd", "") if isinstance(entry, dict) else ""
            if query not in f"{model_id} {name} {command}".casefold():
                continue
            item = QListWidgetItem(f"{model_id}\n{name}" if name else str(model_id))
            item.setData(Qt.ItemDataRole.UserRole, str(model_id))
            item.setData(Qt.ItemDataRole.UserRole + 1, str(command))
            item.setData(Qt.ItemDataRole.UserRole + 2, str(name))
            self.models.addItem(item)
            if item.data(Qt.ItemDataRole.UserRole) == selected:
                self.models.setCurrentItem(item)
        self.status.emit("llama-swap configuration refreshed.")

    def _select(self, item: QListWidgetItem | None) -> None:
        self.current_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not item:
            self.raw.clear()
            return
        command = item.data(Qt.ItemDataRole.UserRole + 1)
        name = item.data(Qt.ItemDataRole.UserRole + 2)
        model_path = self._model_path(command)
        availability = "available" if model_path and Path(model_path).is_file() else "missing or not detected"
        self.details.setText(f"<b>{self.current_id}</b>{'<br>' + name if name else ''}<br>Model: {model_path or 'not detected'} ({availability})")
        self.raw.setPlainText(command)
        try:
            entry = self.service().models().get(self.current_id, {})
            ttl, unload = entry.get("ttl", -1), entry.get("unloadTimeout", 0)
            self.ttl_mode.setCurrentText("Inherit global" if ttl == -1 else "Override")
            self.ttl_value.setText("" if ttl == -1 else str(ttl))
            self.unload_mode.setCurrentText("Inherit global" if unload == 0 else "Override")
            self.unload_value.setText("" if unload == 0 else str(unload))
        except LlamaSwapError:
            pass

    @staticmethod
    def _model_path(command: str) -> str | None:
        import re
        match = re.search(r"(?:-m|--model)\s+(?:\"([^\"]+)\"|(\S+))", command)
        return next((group for group in match.groups() if group), None) if match else None

    def _load(self) -> None:
        if self.current_id:
            self.load_requested.emit(self.raw.toPlainText())

    def _save(self) -> None:
        if not self.current_id:
            return
        try:
            self.service().replace_command(self.current_id, self.raw.toPlainText())
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Save llama-swap command", str(error))
            return
        self.status.emit(f"Saved {self.current_id}; backup created.")
        self.refresh()

    def _duplicate(self) -> None:
        if not self.current_id:
            return
        target, accepted = QInputDialog.getText(self, "Duplicate model", "New model ID:", text=f"{self.current_id}-copy")
        if not accepted or not target.strip():
            return
        try:
            self.service().duplicate(self.current_id, target.strip())
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Duplicate model", str(error))
            return
        self.status.emit(f"Duplicated {self.current_id} as {target.strip()}; backup created.")
        self.refresh()

    def _remove(self) -> None:
        if not self.current_id:
            return
        if QMessageBox.question(self, "Remove llama-swap model", f"Remove {self.current_id!r}? This removes only this model entry.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service().remove_model(self.current_id)
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Remove model", str(error))
            return
        self.status.emit("Model removed; backup created.")
        self.refresh()

    def _load_globals(self, data) -> None:
        defaults = {"healthCheckTimeout": 0, "globalTTL": 0, "unloadTimeout": 0, "startPort": 0}
        for key, field in self.global_numbers.items():
            field.setText(str(data.get(key, defaults[key])))
        self.log_level.setCurrentText(str(data.get("logLevel", "info")))
        self.log_output.setCurrentText(str(data.get("logToStdout", "both")))
        self.include_aliases.setChecked(bool(data.get("includeAliasesInList", False)))

    def _save_globals(self) -> None:
        service = self.service()
        if service is None:
            return
        try:
            values = {key: int(field.text()) for key, field in self.global_numbers.items()}
            values.update({"logLevel": self.log_level.currentText(), "logToStdout": self.log_output.currentText(), "includeAliasesInList": self.include_aliases.isChecked()})
            service.update_globals(values)
        except (ValueError, LlamaSwapError) as error:
            QMessageBox.critical(self, "Save Global Settings", str(error))
            return
        self.status.emit("Saved global settings; backup created.")
        self.refresh()

    def _inherit_timeouts(self) -> None:
        if QMessageBox.question(self, "Inherit global timeouts", "Set every model TTL to -1 and unloadTimeout to 0?") != QMessageBox.StandardButton.Yes:
            return
        try:
            count = self.service().inherit_model_timeouts()
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Inherit global timeouts", str(error))
            return
        self.status.emit(f"Updated {count} models to inherit global TTL/unload settings; backup created.")
        self.refresh()

    def _save_model_settings(self) -> None:
        if not self.current_id:
            return
        try:
            ttl = -1 if self.ttl_mode.currentText() == "Inherit global" else int(self.ttl_value.text())
            unload = 0 if self.unload_mode.currentText() == "Inherit global" else int(self.unload_value.text())
            self.service().update_model_metadata(self.current_id, {"ttl": ttl, "unloadTimeout": unload})
        except (ValueError, LlamaSwapError) as error:
            QMessageBox.critical(self, "Save Model Settings", str(error))
            return
        self.status.emit(f"Saved {self.current_id} model TTL/unload settings; backup created.")
        self.refresh()
