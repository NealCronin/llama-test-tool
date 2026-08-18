from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget, QLabel


class OutputConsole(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Process Output"))
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setMaximumBlockCount(10_000)
        self.text.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.text)

    def append(self, value: str) -> None:
        cursor = self.text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(value)
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()

    def clear(self) -> None:
        self.text.clear()
