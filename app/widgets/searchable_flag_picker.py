from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from app.models.flags import FlagSpec
from app.services.flag_catalog import FlagCatalog


class _FlagRow(QWidget):
    """One picker row: a pin star button plus the flag name, parameter grammar, and description."""

    def __init__(self, spec: FlagSpec, flag: str, pinned: bool, on_pin) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.star = QToolButton()
        self.star.setCheckable(True)
        self.star.setChecked(pinned)
        self.star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star.setFixedSize(20, 20)
        self._update_star(spec, pinned)
        self.star.clicked.connect(lambda checked: on_pin(spec.canonical_name, checked))
        suffix = " ".join(spec.parameter_names)
        label = QLabel(f"{flag} {suffix}\n{spec.description}".strip())
        label.setToolTip(spec.description)
        layout.addWidget(self.star)
        layout.addWidget(label, 1)

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

    def _rank(self, spec: FlagSpec, flag: str, query: str, pin_order: dict[str, int]) -> tuple[int, int, int, str]:
        """Pinned flags outrank unpinned ones; within each class exact matches beat prefixes,
        prefixes beat weaker name/description matches; pin order breaks ties, then flag name."""
        pin_class = 0 if spec.canonical_name in pin_order else 1
        folded = query.casefold()
        if not query:
            kind = 1
        elif flag.casefold() == folded or spec.canonical_name == query:
            kind = 0
        elif flag.casefold().startswith(folded) or spec.canonical_name.casefold().startswith(folded):
            kind = 1
        else:
            kind = 2
        return (pin_class, kind, pin_order.get(spec.canonical_name, 0), flag)

    def populate(self) -> None:
        current = self.results.currentItem().data(Qt.ItemDataRole.UserRole + 1) if self.results.currentItem() else None
        self.results.clear()
        query = self.search.text().strip()
        pin_order = {name: index for index, name in enumerate(self.pinned_flags)}
        entries: list[tuple[FlagSpec, str]] = []
        for spec in self.catalog.search(query):
            flags = (spec.preferred_name, *spec.negative_aliases) if spec.negative_aliases else (spec.preferred_name,)
            entries.extend((spec, flag) for flag in dict.fromkeys(flags))
        entries.sort(key=lambda entry: self._rank(entry[0], entry[1], query, pin_order))
        for spec, flag in entries[:100]:
            item = QListWidgetItem(f"{flag} {' '.join(spec.parameter_names)}".strip())
            item.setData(Qt.ItemDataRole.UserRole, spec)
            item.setData(Qt.ItemDataRole.UserRole + 1, flag)
            row = _FlagRow(spec, flag, spec.canonical_name in pin_order, self._toggle_pin)
            item.setSizeHint(row.sizeHint())
            self.results.addItem(item)
            self.results.setItemWidget(item, row)
            if flag == current:
                self.results.setCurrentItem(item)
        if self.results.count() and self.results.currentItem() is None:
            self.results.setCurrentRow(0)

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
