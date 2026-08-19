"""A small YAML/JSON text editor used for free-form structured values."""
from __future__ import annotations

import io

from PySide6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from ruamel.yaml import YAML

yaml = YAML(typ="rt")
yaml.preserve_quotes = True


class StructuredYamlEditor(QWidget):
    """Editable YAML (JSON also parses); blank means 'not configured'."""

    def __init__(self, placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QTextEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setAcceptRichText(False)
        self.edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.edit, 1)
        self.status = QLabel("", self)
        self.status.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.status)
        self.edit.textChanged.connect(self._revalidate)

    def set_object(self, value) -> None:
        self.edit.blockSignals(True)
        if value is None or value == {}:
            self.edit.clear()
        else:
            stream = io.StringIO()
            yaml.dump(value, stream)
            self.edit.setPlainText(stream.getvalue().strip() + "\n")
        self.edit.blockSignals(False)
        self._revalidate()

    def object(self):
        text = self.edit.toPlainText().strip()
        if not text:
            return None
        try:
            return yaml.load(text)
        except Exception as error:
            raise ValueError(f"Structured YAML is not valid: {error}") from error

    def _revalidate(self) -> None:
        try:
            self.object()
            self.status.setText("Valid YAML." if self.edit.toPlainText().strip() else "")
            self.status.setStyleSheet("color: #15803d;")
        except ValueError:
            self.status.setText("Invalid YAML.")
            self.status.setStyleSheet("color: #b91c1c;")
