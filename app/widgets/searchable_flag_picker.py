from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from app.models.flags import FlagSpec
from app.services.flag_catalog import FlagCatalog


class SearchableFlagPicker(QDialog):
    def __init__(self, catalog: FlagCatalog, parent=None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.selected: FlagSpec | None = None
        self.setWindowTitle("Add llama.cpp argument")
        self.resize(700, 440)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText="Search flag names, aliases, or descriptions…")
        self.results = QListWidget()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(self.search)
        layout.addWidget(self.results)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.populate)
        self.results.itemDoubleClicked.connect(lambda _: self.accept())
        self.results.currentItemChanged.connect(lambda *_: buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.results.currentItem() is not None))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.populate()

    def populate(self) -> None:
        current = self.results.currentItem().data(Qt.ItemDataRole.UserRole) if self.results.currentItem() else None
        self.results.clear()
        for spec in self.catalog.search(self.search.text())[:100]:
            suffix = " ".join(spec.parameter_names)
            text = f"{', '.join(spec.aliases)} {suffix}\n{spec.description}".strip()
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, spec)
            item.setToolTip(spec.description)
            self.results.addItem(item)
            if spec.canonical_name == current:
                self.results.setCurrentItem(item)
        if self.results.count() and self.results.currentItem() is None:
            self.results.setCurrentRow(0)

    def accept(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        super().accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.search.setFocus()
