from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit


class MemoryTestOptionsDialog(QDialog):
    """Optional overrides only; blank values retain the installed llama.cpp defaults."""

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Memory Test Options")
        layout = QFormLayout(self)
        layout.addRow(QLabel("Leave both values blank to use llama.cpp's normal fitting defaults."))
        self.fit_target = QLineEdit(settings.memory_fit_target)
        self.fit_target.setPlaceholderText("llama.cpp default")
        self.fit_context = QLineEdit(settings.memory_fit_context)
        self.fit_context.setPlaceholderText("llama.cpp default")
        layout.addRow("Free-memory target (MiB per device)", self.fit_target)
        layout.addRow("Minimum fitted context", self.fit_context)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def save_to(self, settings) -> None:
        settings.memory_fit_target = self.fit_target.text().strip()
        settings.memory_fit_context = self.fit_context.text().strip()
        settings.save()
