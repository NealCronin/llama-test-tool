"""Presence-aware editors for optional llama-swap settings.

Each widget distinguishes three states: absent (use the effective default), explicitly
configured, and reset. Displaying a default never implies writing it; ``explicit()``
returns ``None`` until the user deliberately sets a value.
"""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QWidget


class OptionalSettingWidget(QWidget):
    """A value editor with a 'set explicitly' toggle and effective-default display."""

    def __init__(self, default, placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        self._default = default
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.set_explicit = QCheckBox("Set", self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.set_explicit)
        self.set_explicit.toggled.connect(self.edit.setEnabled)
        self.edit.setEnabled(False)

    def load(self, present: bool, value) -> None:
        if present:
            self.edit.setText(self._to_text(value))
            self.set_explicit.setChecked(True)
        else:
            self.edit.setText(self._to_text(self._default))
            self.set_explicit.setChecked(False)

    def effective(self):
        return self._default if not self.set_explicit.isChecked() else self._parse(self.edit.text())

    def explicit(self):
        return None if not self.set_explicit.isChecked() else self._parse(self.edit.text())

    def reset(self) -> None:
        self.edit.setText(self._to_text(self._default))
        self.set_explicit.setChecked(False)

    @staticmethod
    def _to_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _parse(self, text: str) -> object:
        return text.strip()


class OptionalInt(OptionalSettingWidget):
    def __init__(self, default: int, parent=None) -> None:
        super().__init__(default, placeholder=str(default), parent=parent)

    def _parse(self, text: str) -> int:
        try:
            return int(text.strip())
        except ValueError as error:
            raise ValueError("Must be a whole number.") from error


class OptionalBool(QWidget):
    """Three-state boolean: effective default, explicit true, explicit false."""

    def __init__(self, default: bool, parent=None) -> None:
        super().__init__(parent)
        self._default = default
        self.choice = QComboBox(self)
        self.choice.addItem(f"Default ({'true' if default else 'false'})", None)
        self.choice.addItem("true", True)
        self.choice.addItem("false", False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.choice, 1)

    def load(self, present: bool, value: bool) -> None:
        self.choice.setCurrentIndex(1 if value else 2 if present else 0)

    def effective(self) -> bool:
        value = self.choice.currentData()
        return bool(self._default if value is None else value)

    def explicit(self) -> bool | None:
        return self.choice.currentData()

    def reset(self) -> None:
        self.choice.setCurrentIndex(0)


class OptionalChoice(QWidget):
    """A fixed set of values plus a leading 'use default' entry."""

    def __init__(self, items, default, parent=None) -> None:
        super().__init__(parent)
        self._default = default
        self.choice = QComboBox(self)
        self.choice.addItem(f"Default ({default})" if default else "Default (none)", None)
        for item in items:
            if item != default:
                self.choice.addItem(str(item), item)
        if default not in items:
            self.choice.addItem(str(default), default)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.choice, 1)

    def load(self, present: bool, value) -> None:
        index = self.choice.findData(value) if present else 0
        self.choice.setCurrentIndex(index if index >= 0 else 0)

    def effective(self):
        value = self.choice.currentData()
        return self._default if value is None else value

    def explicit(self):
        return self.choice.currentData()

    def reset(self) -> None:
        self.choice.setCurrentIndex(0)
