from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
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
            entries = service.models()
        except LlamaSwapError as error:
            self.details.setText(str(error))
            return
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
