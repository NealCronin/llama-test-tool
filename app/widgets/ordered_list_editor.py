"""Ordered, editable collections for llama-swap lists and mappings."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class OrderedStringListEditor(QWidget):
    """An ordered string list with add/remove/move controls and optional known choices."""

    def __init__(self, choices: tuple[str, ...] = (), placeholder: str = "", use_combo: bool = False, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)
        add_row = QWidget(self)
        add_layout = QHBoxLayout(add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        if choices or use_combo:
            self.add_input: QComboBox | QLineEdit = QComboBox(self)
            self.add_input.setEditable(True)
            if choices:
                self.add_input.addItems(choices)
        else:
            self.add_input = QLineEdit(self)
            self.add_input.setPlaceholderText(placeholder)
        add = QPushButton("Add")
        add.clicked.connect(self.add)
        add_layout.addWidget(self.add_input, 1)
        add_layout.addWidget(add)
        buttons = QHBoxLayout()
        self.remove = QPushButton("Remove")
        self.up = QPushButton("Move Up")
        self.down = QPushButton("Move Down")
        self.remove.clicked.connect(self.remove_selected)
        self.up.clicked.connect(lambda: self.move_selected(-1))
        self.down.clicked.connect(lambda: self.move_selected(1))
        for button in (self.remove, self.up, self.down):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.list.currentRowChanged.connect(self._update_actions)
        self._update_actions(-1)

    def add(self) -> None:
        input_widget = self.add_input
        value = input_widget.currentText().strip() if isinstance(input_widget, QComboBox) else input_widget.text().strip()
        if not value:
            return
        item = QListWidgetItem(value)
        self.list.addItem(item)
        self.list.setCurrentItem(item)
        if isinstance(input_widget, QComboBox):
            input_widget.setEditText("")
            input_widget.setCurrentIndex(-1)
        else:
            input_widget.clear()

    def set_choices(self, choices) -> None:
        widget = self.add_input
        if isinstance(widget, QComboBox):
            widget.clear()
            widget.addItems(str(choice) for choice in choices)
        else:
            widget.clear()
    def remove_selected(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.list.takeItem(self.list.row(item))

    def move_selected(self, offset: int) -> None:
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def _update_actions(self, _row: int) -> None:
        count = self.list.count()
        row = self.list.currentRow()
        self.remove.setEnabled(row >= 0)
        self.up.setEnabled(0 < row)
        self.down.setEnabled(0 <= row < count - 1)

    def set_values(self, values) -> None:
        self.list.clear()
        for value in values:
            self.list.addItem(QListWidgetItem(str(value)))
        self._update_actions(-1)

    def values(self) -> list[str]:
        return [self.list.item(index).text() for index in range(self.list.count())]


class KeyValueTableEditor(QWidget):
    """An editable key/value table; ordering is preserved."""

    def __init__(self, columns: tuple[str, ...] = ("Key", "Value"), parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(columns), self)
        self.table.setHorizontalHeaderLabels(list(columns))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        add = QPushButton("Add Row")
        add.clicked.connect(self.add_row)
        self.remove = QPushButton("Remove Row")
        self.remove.clicked.connect(self.remove_row)
        self.up = QPushButton("Move Up")
        self.up.clicked.connect(lambda: self.move_row(-1))
        self.down = QPushButton("Move Down")
        self.down.clicked.connect(lambda: self.move_row(1))
        for button in (add, self.remove, self.up, self.down):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.table.cellChanged.connect(self._update_actions)

    def add_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column in range(self.table.columnCount()):
            self.table.setItem(row, column, QTableWidgetItem(""))
        self.table.setCurrentCell(row, 0)

    def remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def move_row(self, offset: int) -> None:
        row = self.table.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        items = [self.table.takeItem(row, column) for column in range(self.table.columnCount())]
        if row < target:
            target -= 1
        for column, item in enumerate(items):
            self.table.setItem(target, column, item)
        self.table.setCurrentCell(target, 0)

    def _update_actions(self, *_args) -> None:
        row = self.table.currentRow()
        count = self.table.rowCount()
        self.remove.setEnabled(row >= 0)
        self.up.setEnabled(0 < row)
        self.down.setEnabled(0 <= row < count - 1)

    def set_items(self, items) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for item in items:
            self.add_row()
            row = self.table.rowCount() - 1
            for column, value in enumerate(item[: self.table.columnCount()]):
                self.table.setItem(row, column, QTableWidgetItem("" if value is None else str(value)))
        self.table.blockSignals(False)

    def items(self) -> list[tuple[str, str]]:
        result = []
        for row in range(self.table.rowCount()):
            cells = tuple((self.table.item(row, column).text() if (cell := self.table.item(row, column)) is not None else "") for column in range(self.table.columnCount()))
            result.append(cells)
        return result

    def current_row(self) -> int:
        return self.table.currentRow()


class OrderedSecretListEditor(QWidget):
    """Ordered list of secret values (API keys): rows stay masked; the selected row is editable."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget(self)
        layout.addWidget(self.list, 1)
        self.detail = QLineEdit(self)
        self.detail.setPlaceholderText("Select a key to edit it. Values are kept as-is when unchanged.")
        layout.addWidget(self.detail)
        add_row = QWidget(self)
        add_layout = QHBoxLayout(add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        self.new_value = QLineEdit(self)
        self.new_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_value.setPlaceholderText("New key (supports ${env.NAME})")
        add = QPushButton("Add")
        add.clicked.connect(self.add)
        add_layout.addWidget(self.new_value, 1)
        add_layout.addWidget(add)
        layout.addWidget(add_row)
        buttons = QHBoxLayout()
        self.remove = QPushButton("Remove")
        self.up = QPushButton("Move Up")
        self.down = QPushButton("Move Down")
        self.remove.clicked.connect(self.remove_selected)
        self.up.clicked.connect(lambda: self.move_selected(-1))
        self.down.clicked.connect(lambda: self.move_selected(1))
        for button in (self.remove, self.up, self.down):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.list.currentRowChanged.connect(self._selection_changed)
        self._selection_changed(-1)

    @staticmethod
    def _mask(value: str) -> str:
        return "••••••••" if value else "(empty)"

    def add(self) -> None:
        value = self.new_value.text()
        if not value:
            return
        item = QListWidgetItem(self._mask(value))
        item.setData(Qt.ItemDataRole.UserRole, value)
        self.list.addItem(item)
        self.list.setCurrentItem(item)
        self.new_value.clear()

    def remove_selected(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.list.takeItem(self.list.row(item))

    def move_selected(self, offset: int) -> None:
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def _selection_changed(self, row: int) -> None:
        self.detail.blockSignals(True)
        item = self.list.currentItem()
        self.detail.setText(item.data(Qt.ItemDataRole.UserRole) if item is not None else "")
        self.detail.setEnabled(item is not None)
        self.detail.blockSignals(False)
        count = self.list.count()
        self.remove.setEnabled(row >= 0)
        self.up.setEnabled(0 < row)
        self.down.setEnabled(0 <= row < count - 1)

    def set_values(self, values) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for value in values:
            item = QListWidgetItem(self._mask(str(value)))
            item.setData(Qt.ItemDataRole.UserRole, str(value))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._selection_changed(-1)

    def values(self) -> list[str]:
        result = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            value = self.detail.text() if item is self.list.currentItem() else item.data(Qt.ItemDataRole.UserRole)
            result.append(value)
        return result
