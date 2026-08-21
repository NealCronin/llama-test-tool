from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from app.models.flags import FlagSpec
from app.services.flag_catalog import FlagCatalog


class _FlagRow(QWidget):
    """One picker row: a pin star button and a bold flag line."""

    def __init__(self, spec: FlagSpec, flag: str, pinned: bool, on_pin, width: int) -> None:
        super().__init__()
        self._width = width
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 8, 4)
        layout.setSpacing(8)
        self.star = QToolButton()
        self.star.setCheckable(True)
        self.star.setChecked(pinned)
        self.star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star.setFixedSize(20, 20)
        self._update_star(spec, pinned)
        self.star.clicked.connect(lambda checked: on_pin(spec.canonical_name, checked))
        self.flag_label = QLabel(flag)
        flag_font = self.flag_label.font()
        flag_font.setBold(True)
        self.flag_label.setFont(flag_font)
        layout.addWidget(self.star)
        layout.addWidget(self.flag_label, 1)

    def sizeHint(self) -> QSize:
        height = max(self.flag_label.sizeHint().height(), self.star.height()) + 8
        return QSize(max(self._width, 10), max(height, 30))

    def _update_star(self, spec: FlagSpec, pinned: bool) -> None:
        self.star.setText("★" if pinned else "☆")
        verb = "Unpin" if pinned else "Pin"
        self.star.setToolTip(f"{verb} {spec.canonical_name}")
        self.star.setAccessibleName(f"{verb} {spec.canonical_name}")


class SearchableFlagPicker(QDialog):
    def __init__(self, catalog: FlagCatalog, pinned_flags: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.selected: FlagSpec | None = None
        self.selected_flag: str | None = None
        # Canonical names in pin order; the dialog mutates this list as the user pins/unpins.
        self.pinned_flags: list[str] = list(pinned_flags or [])
        self.setWindowTitle("Add llama.cpp argument")
        self.resize(700, 440)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText="Search flag names, aliases, or descriptions…")
        self.results = QListWidget()
        self.results.viewport().installEventFilter(self)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(self.search)
        layout.addWidget(self.results)
        self.search.textChanged.connect(self.populate)
        self.results.itemDoubleClicked.connect(lambda _: self.accept())
        self.results.currentItemChanged.connect(lambda *_: buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.results.currentItem() is not None))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._rebuilding = False
        self.populate()

    def _rank(self, spec: FlagSpec, flag: str, query: str, pin_order: dict[str, int]) -> tuple[int, int, int, str]:
        """Pinned flags outrank unpinned ones; within each class, exact matches on any
        documented alias or the canonical name beat prefix matches, which beat weaker
        name/description matches; pin order then flag name break ties."""
        pin_class = 0 if spec.canonical_name in pin_order else 1
        folded = query.casefold()
        if not query:
            kind = 1
        else:
            names = (spec.canonical_name, *spec.aliases, flag)
            exact = any(name.casefold() == folded for name in names)
            prefix = any(name.casefold().startswith(folded) for name in names)
            kind = 0 if exact else (1 if prefix else 2)
        return (pin_class, kind, pin_order.get(spec.canonical_name, 0), flag)
    def populate(self) -> None:
        current = self.results.currentItem().data(Qt.ItemDataRole.UserRole + 1) if self.results.currentItem() else None
        self._rebuilding = True
        try:
            self.results.clear()
            query = self.search.text().strip()
            pin_order = {name: index for index, name in enumerate(self.pinned_flags)}
            entries: list[tuple[FlagSpec, str]] = []
            for spec in self.catalog.search(query):
                flags = (spec.preferred_name, *spec.negative_aliases) if spec.negative_aliases else (spec.preferred_name,)
                entries.extend((spec, flag) for flag in dict.fromkeys(flags))
            entries.sort(key=lambda entry: self._rank(entry[0], entry[1], query, pin_order))
            width = self._row_width()
            for spec, flag in entries:
                item = QListWidgetItem("")  # the row widget draws the flag; item text would double-draw behind it
                item.setData(Qt.ItemDataRole.UserRole, spec)
                item.setData(Qt.ItemDataRole.UserRole + 1, flag)
                row = _FlagRow(spec, flag, spec.canonical_name in pin_order, self._toggle_pin, width)
                self.results.addItem(item)
                self.results.setItemWidget(item, row)
                item.setSizeHint(row.sizeHint())
                if flag == current:
                    self.results.setCurrentItem(item)
            if self.results.count() and self.results.currentItem() is None:
                self.results.setCurrentRow(0)
        finally:
            self._rebuilding = False
        self._rows_resized()  # sync row widths to the viewport now that the scrollbar state is settled

    def _row_width(self) -> int:
        return max(self.results.viewport().width(), self.width() - 40, 320)

    def _rows_resized(self) -> None:
        if self._rebuilding:
            return  # populate rebuilds the rows itself; a resize mid-rebuild must not re-enter
        width = self._row_width()
        for index in range(self.results.count()):
            item = self.results.item(index)
            row = self.results.itemWidget(item)
            if row is not None:
                row._width = width
                item.setSizeHint(row.sizeHint())
        self.results.doItemsLayout()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.results.viewport() and event.type() == QEvent.Type.Resize:
            self._rows_resized()
        return super().eventFilter(watched, event)

    def _toggle_pin(self, canonical_name: str, pinned: bool) -> None:
        if pinned and canonical_name not in self.pinned_flags:
            self.pinned_flags.append(canonical_name)
        elif not pinned and canonical_name in self.pinned_flags:
            self.pinned_flags.remove(canonical_name)
        self.populate()

    def accept(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        self.selected_flag = item.data(Qt.ItemDataRole.UserRole + 1)
        super().accept()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.search.setFocus()
