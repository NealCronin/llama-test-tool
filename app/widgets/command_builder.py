from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPoint, QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.models.command import Command, CommandArgument
from app.models.flags import FlagSpec
from app.services.flag_catalog import FlagCatalog
from app.services.validation import validate_command
from app.server import SERVER_COMMAND
from app.settings import AppSettings
from app.widgets.argument_row import ArgumentRow
from app.widgets.searchable_flag_picker import SearchableFlagPicker
from app.widgets.server_verify_status import ServerVerifyStatusPanel


class CommandBuilder(QWidget):
    changed = Signal()
    add_to_swap_requested = Signal()
    test_requested = Signal()
    stop_requested = Signal()
    memory_test_requested = Signal()
    memory_options_requested = Signal()
    memory_cancel_requested = Signal()
    benchmark_requested = Signal()
    benchmark_options_requested = Signal()
    benchmark_cancel_requested = Signal()

    def __init__(self, settings: AppSettings, catalog: FlagCatalog, parent=None) -> None:
        super().__init__(parent)
        self.settings, self.catalog = settings, catalog
        self.command = Command.from_dict(settings.last_command) if settings.last_command else Command(executable=SERVER_COMMAND)
        self.command.executable = SERVER_COMMAND
        self.rows: list[ArgumentRow] = []
        self.spacer_rows: list[_SpacerRow] = []
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("<h2>Command Builder</h2>"))
        heading.addStretch()
        self.catalog_status = QLabel(f"Argument catalog: {catalog.source} ({len(catalog.specs)} flags)")
        heading.addWidget(self.catalog_status)
        layout.addLayout(heading)

        self.arguments_host = _RowDropHost(self)
        self.arguments_layout = QVBoxLayout(self.arguments_host)
        self.arguments_layout.setContentsMargins(0, 0, 0, 0)
        self.arguments_layout.addStretch()
        self.scroll_area = QScrollArea(widgetResizable=True)
        self.scroll_area.setWidget(self.arguments_host)
        layout.addWidget(self.scroll_area, 1)
        self.visible_row_widgets: list[QWidget] = []
        self._drop_indicator = QFrame()
        self._drop_indicator.setFixedHeight(2)
        self._drop_indicator.setStyleSheet("background: #2563eb; border: none;")

        button_row = QHBoxLayout()
        add = QPushButton("+ Add Argument")
        add.clicked.connect(self.add_argument)
        self.spacer_button = QPushButton("+ Spacer")
        self.spacer_button.setToolTip("Insert a visual separator between two argument rows. It never changes the command.")
        self.spacer_button.clicked.connect(self.add_spacer)
        button_row.addWidget(add)
        button_row.addWidget(self.spacer_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        controls = QHBoxLayout()
        self.preview_vertical = QCheckBox("Vertical preview")
        self.preview_vertical.setChecked(settings.vertical_preview)
        self.preview_vertical.toggled.connect(self._changed)
        self.copy = QPushButton("Copy Command")
        self.clear = QPushButton("Clear Command")
        self.memory_test = QPushButton("Memory Test")
        self.benchmark = QPushButton("Benchmark")
        self.benchmark_options = QPushButton("Benchmark Options")
        self.benchmark_cancel = QPushButton("Cancel Benchmark")
        self.benchmark_cancel.setEnabled(False)
        self.memory_options = QPushButton("Memory Test Options")
        self.memory_cancel = QPushButton("Cancel Memory Test")
        self.memory_cancel.setEnabled(False)
        self.test = QPushButton("Test Server")
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.add_swap = QPushButton("Add to llama-swap")
        for button in (self.preview_vertical, self.memory_test, self.benchmark, self.benchmark_options, self.benchmark_cancel, self.memory_options, self.memory_cancel, self.test, self.stop, self.copy, self.clear, self.add_swap):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self.verify_status = ServerVerifyStatusPanel(self)
        layout.addWidget(self.verify_status)
        self.preview = QLabel()
        self.preview.setTextInteractionFlags(self.preview.textInteractionFlags() | self.preview.textInteractionFlags().TextSelectableByMouse)
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("background: #111827; color: #e5e7eb; font-family: Consolas, monospace; padding: 10px; border-radius: 4px;")
        layout.addWidget(QLabel("Command Preview"))
        layout.addWidget(self.preview)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)

        self.copy.clicked.connect(self.copy_command)
        self.clear.clicked.connect(self.clear_command)
        self.test.clicked.connect(self.test_requested)
        self.stop.clicked.connect(self.stop_requested)
        self.add_swap.clicked.connect(self.add_to_swap_requested)
        self.memory_test.clicked.connect(self.memory_test_requested)
        self.memory_options.clicked.connect(self.memory_options_requested)
        self.memory_cancel.clicked.connect(self.memory_cancel_requested)
        self.benchmark.clicked.connect(self.benchmark_requested)
        self.benchmark_options.clicked.connect(self.benchmark_options_requested)
        self.benchmark_cancel.clicked.connect(self.benchmark_cancel_requested)
        self.rebuild()

    def set_catalog(self, catalog: FlagCatalog) -> None:
        self.catalog = catalog
        self.catalog_status.setText(f"Argument catalog: {catalog.source} ({len(catalog.specs)} flags)")
        self.rebuild()

    def refresh_folder_for(self, canonical_name: str) -> bool:
        """Re-scan a folder-backed row's options in place."""
        for row in self.rows:
            if row.spec.canonical_name == canonical_name:
                row.refresh_folder()
                return True
        return False

    def rebuild(self) -> None:
        self.hide_drop_indicator()
        while self.rows:
            row = self.rows.pop()
            self.arguments_layout.removeWidget(row)
            row.deleteLater()
        while self.spacer_rows:
            spacer = self.spacer_rows.pop()
            self.arguments_layout.removeWidget(spacer)
            spacer.deleteLater()
        self.visible_row_widgets = []
        self._normalize_spacers()
        spacers = set(self.settings.builder_spacers)
        for position, argument in enumerate(self.command.arguments, start=1):
            spec = self.catalog.find(argument.flag)
            if spec is None:
                spec = FlagSpec(argument.flag, (argument.flag,), "Unknown argument retained from saved/imported command.", len(argument.values))
            removable = argument.flag not in {"-m", "--model"}
            row = ArgumentRow(argument, spec, self.settings, removable=removable, parent=self.arguments_host)
            row.changed.connect(self._changed)
            row.remove_requested.connect(self.remove_row)
            row.move_requested.connect(self.move_row)
            self.arguments_layout.insertWidget(self.arguments_layout.count() - 1, row)
            row_index = len(self.rows)
            self.rows.append(row)
            row.layout().insertWidget(0, _DragGrip(removable, lambda index=row_index: self._start_row_drag(row, "argument", index), parent=row))
            self.visible_row_widgets.append(row)
            if position in spacers:
                spacer = _SpacerRow(position, len(self.command.arguments) - 1, self.move_spacer, self.remove_spacer, lambda index=len(self.spacer_rows): self._start_row_drag(spacer, "spacer", index))
                self.arguments_layout.insertWidget(self.arguments_layout.count() - 1, spacer)
                self.spacer_rows.append(spacer)
                self.visible_row_widgets.append(spacer)
        self.spacer_button.setEnabled(len(self.command.arguments) >= 2)
        self._changed()

    def add_argument(self) -> None:
        picker = SearchableFlagPicker(self.catalog, self.settings.pinned_flags, self)
        picker.exec()
        if picker.pinned_flags != self.settings.pinned_flags:
            self.settings.pinned_flags = picker.pinned_flags
            self._changed()
        if picker.selected:
            self.add_spec(picker.selected, flag=picker.selected_flag)

    def add_spec(self, spec: FlagSpec, values: list[str] | None = None, source_type: str = "manual", flag: str | None = None) -> None:
        if spec.canonical_name == "--model":
            return
        if spec.canonical_name in {"--chat-template", "--chat-template-file"} and values and values[0]:
            self._ensure_jinja_before_template()
        initial_values = values if values is not None else ([] if spec.optional_parameter else [""] * spec.parameter_count)
        self.command.arguments.append(CommandArgument(flag or spec.preferred_name, initial_values, source_type))
        self.rebuild()

    def set_argument(self, canonical_name: str, values: list[str], source_type: str = "preset") -> None:
        spec = self.catalog.find(canonical_name)
        if spec is None:
            return
        if spec.canonical_name in {"--chat-template", "--chat-template-file"} and values and values[0]:
            self._ensure_jinja_before_template()
        existing = next((argument for argument in self.command.arguments if (current := self.catalog.find(argument.flag)) and current.canonical_name == spec.canonical_name), None)
        if existing is None:
            self.command.arguments.append(CommandArgument(spec.preferred_name, list(values), source_type))
        else:
            existing.values, existing.source_type = list(values), source_type
        self.rebuild()

    def _ensure_jinja_before_template(self) -> None:
        if self.catalog.find("--jinja") is None:
            return
        self.command.arguments = [argument for argument in self.command.arguments if not ((current := self.catalog.find(argument.flag)) and current.canonical_name == "--jinja")]
        template_index = next((i for i, arg in enumerate(self.command.arguments) if (current := self.catalog.find(arg.flag)) and current.canonical_name in {"--chat-template", "--chat-template-file"}), len(self.command.arguments))
        self.command.arguments.insert(template_index, CommandArgument("--jinja"))

    def apply_preset(self, preset: str) -> None:
        lookup = {
            "mmproj": ("--mmproj", [""], "mmproj"),
            "mtp": ("--spec-type", ["draft-mtp"], "preset"),
            "ngram": ("--spec-type", ["ngram-mod"], "preset"),
            "template": ("--chat-template-file", [""], "template_file"),
        }
        name, values, source = lookup[preset]
        self.set_argument(name, values, source_type=source)

    def remove_row(self, row: ArgumentRow) -> None:
        self.command.arguments.remove(row.argument)
        self.rebuild()

    def reorder_argument(self, source_index: int, target_index: int) -> bool:
        """Move one real argument to another 0-based position; the model argument never moves."""
        arguments = self.command.arguments
        if not (0 <= source_index < len(arguments)) or not (1 <= target_index < len(arguments)):
            return False
        if arguments[source_index].flag in {"-m", "--model"} or source_index == target_index:
            return False
        argument = arguments.pop(source_index)
        arguments.insert(target_index, argument)
        self.rebuild()
        return True

    def move_row(self, row: ArgumentRow, offset: int) -> None:
        index = self.command.arguments.index(row.argument)
        if row.argument.flag in {"-m", "--model"}:
            return
        self.reorder_argument(index, index + offset)

    def _normalize_spacers(self) -> None:
        """Permanently prune persisted spacer boundaries the current command can no longer host."""
        valid = len(self.command.arguments) - 1
        pruned = sorted({boundary for boundary in self.settings.builder_spacers if 1 <= boundary <= valid})
        if pruned != self.settings.builder_spacers:
            self.settings.builder_spacers = pruned

    def add_spacer(self) -> None:
        spacers = set(self.settings.builder_spacers)
        for boundary in range(len(self.command.arguments) - 1, 0, -1):
            if boundary not in spacers:
                spacers.add(boundary)
                self.settings.builder_spacers = sorted(spacers)
                self.rebuild()
                return

    def set_spacer_boundary(self, boundary: int, target: int) -> bool:
        """Move a spacer from one boundary to another; duplicate and out-of-range targets are rejected."""
        if target == boundary:
            return False
        if not (1 <= target <= len(self.command.arguments) - 1):
            return False
        spacers = set(self.settings.builder_spacers)
        if boundary not in spacers or target in spacers:
            return False
        spacers.discard(boundary)
        spacers.add(target)
        self.settings.builder_spacers = sorted(spacers)
        self.rebuild()
        return True

    def move_spacer(self, boundary: int, offset: int) -> None:
        self.set_spacer_boundary(boundary, boundary + offset)

    def remove_spacer(self, boundary: int) -> None:
        if boundary not in self.settings.builder_spacers:
            return
        spacers = set(self.settings.builder_spacers)
        spacers.discard(boundary)
        self.settings.builder_spacers = sorted(spacers)
        self.rebuild()

    # ------------------------------------------------ drag and drop

    def _start_row_drag(self, row: QWidget, kind: str, index: int) -> None:
        mime = QMimeData()
        mime.setData(ROW_DRAG_MIME, json.dumps({"kind": kind, "index": index}).encode("utf-8"))
        drag = QDrag(row)
        drag.setMimeData(mime)
        drag.setPixmap(self._drag_pixmap(row, kind))
        drag.exec_(Qt.DropAction.MoveAction)  # blocks until drop or cancel
        self.hide_drop_indicator()

    @staticmethod
    def _drag_pixmap(row: QWidget, kind: str) -> QPixmap:
        text = "spacer" if kind == "spacer" else (row.flag_label.text() if isinstance(row, ArgumentRow) else "spacer")
        pixmap = QPixmap(140, 22)
        pixmap.fill(QColor(17, 24, 39))
        painter = QPainter(pixmap)
        painter.setPen(QColor(229, 231, 235))
        painter.drawText(pixmap.rect().adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return pixmap

    def show_drop_indicator(self, element: QWidget, where: str) -> None:
        index = self.arguments_layout.indexOf(element)
        if where == "after":
            index += 1
        self.arguments_layout.insertWidget(index, self._drop_indicator)

    def hide_drop_indicator(self) -> None:
        if self._drop_indicator.parent() is not None:
            self.arguments_layout.removeWidget(self._drop_indicator)
            self._drop_indicator.setParent(None)

    def _element_at(self, position) -> tuple[str, QWidget | None]:
        """Map a host-local position to ("before"|"after", visible row) using each row's midline."""
        rows = self.visible_row_widgets
        if not rows:
            return "after", None
        for element in rows:
            top = element.mapTo(self.arguments_host, QPoint(0, 0)).y()
            bottom = top + element.height()
            if top <= position.y() < bottom:
                return ("before" if position.y() < top + element.height() / 2 else "after"), element
        return "after", rows[-1]

    def _drop_landing(self, payload: dict, position) -> tuple[str, int] | None:
        """Validate a drop; return (kind, target) where target is a final argument index or a spacer boundary."""
        kind = payload.get("kind")
        count = len(self.command.arguments)
        if count < 2:
            return None
        where, element = self._element_at(position)
        if element is None or element not in self.visible_row_widgets:
            return None
        if where == "before" and element is self.visible_row_widgets[0]:
            return None  # nothing may land above the model row
        if isinstance(element, _SpacerRow):
            target = element.boundary if where == "before" else element.boundary + 1
        else:
            argument_index = self.rows.index(element)
            target = argument_index if where == "before" else argument_index + 1
        if kind == "argument":
            source = payload.get("index", -1)
            if not (0 <= source < len(self.rows)) or self.rows[source].argument.flag in {"-m", "--model"}:
                return None
            if not (1 <= target <= count):
                return None  # target is an insertion slot: 1..count, where count means "after the last row"
            final = target - 1 if source < target else target
            if not (1 <= final < count):
                return None
            return "argument", final
        if kind == "spacer":
            source_index = payload.get("index", -1)
            if not (0 <= source_index < len(self.spacer_rows)) or self.spacer_rows[source_index].boundary not in self.settings.builder_spacers:
                return None
            if not (1 <= target <= count - 1) or target in self.settings.builder_spacers:
                return None
            return "spacer", target
        return None

    def drop_target_for(self, payload: dict, position) -> bool:
        """Show the drop indicator when the drag can land at `position`; otherwise hide it."""
        landing = self._drop_landing(payload, position)
        if landing is None:
            self.hide_drop_indicator()
            return False
        where, element = self._element_at(position)
        self.show_drop_indicator(element, where)
        return True

    def perform_drop(self, payload: dict, position) -> None:
        landing = self._drop_landing(payload, position)
        self.hide_drop_indicator()
        if landing is None:
            return
        kind, target = landing
        if kind == "argument":
            self.reorder_argument(payload["index"], target)
        else:
            self.set_spacer_boundary(self.spacer_rows[payload["index"]].boundary, target)

    def load_command(self, command: Command) -> None:
        """Replace the visible command (imported from llama-swap); resets the spacer layout."""
        self.command = command
        self.settings.builder_spacers = []
        self.rebuild()

    def clear_command(self) -> None:
        if QMessageBox.question(self, "Clear command", "Remove every argument except the model selector?") != QMessageBox.StandardButton.Yes:
            return
        self.command.reset()
        self.settings.builder_spacers = []
        self.rebuild()

    def set_running(self, running: bool) -> None:
        self.test.setEnabled(not running)
        self.stop.setEnabled(running)

    def set_memory_running(self, running: bool) -> None:
        self.memory_test.setEnabled(not running)
        self.memory_options.setEnabled(not running)
        self.memory_cancel.setEnabled(running)


    def set_benchmark_running(self, running: bool) -> None:
        self.benchmark.setEnabled(not running)
        self.benchmark_options.setEnabled(not running)
        self.benchmark_cancel.setEnabled(running)
    def copy_command(self) -> None:
        QGuiApplication.clipboard().setText(self.command.rendered(vertical=self.preview_vertical.isChecked()))

    def _changed(self, *_args) -> None:
        self.settings.vertical_preview = self.preview_vertical.isChecked()
        self.command.ensure_model_argument()
        self.preview.setText(self.command.rendered(vertical=self.preview_vertical.isChecked()))
        issues = validate_command(self.command, self.catalog)
        if issues:
            rendered = "<br>".join(f"<b>{issue.severity.title()}:</b> {issue.message}" for issue in issues)
            self.validation.setText(rendered)
            self.validation.setStyleSheet("color: #b45309;" if all(issue.severity == "warning" for issue in issues) else "color: #b91c1c;")
        else:
            self.validation.setText("Ready to test or add to llama-swap.")
            self.validation.setStyleSheet("color: #15803d;")
        self.changed.emit()


class _SpacerRow(QWidget):
    """Presentation-only separator between two argument rows; never part of the command."""
    def __init__(self, boundary: int, max_boundary: int, on_move, on_remove, start_drag) -> None:
        super().__init__()
        self.boundary = boundary
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(2)
        controls.addWidget(_DragGrip(True, start_drag, parent=self))
        controls.addStretch()
        up = QPushButton("↑", self)
        down = QPushButton("↓", self)
        remove = QPushButton("×", self)
        up.setToolTip("Move spacer up")
        down.setToolTip("Move spacer down")
        remove.setToolTip("Remove spacer")
        up.setEnabled(boundary > 1)
        down.setEnabled(boundary < max_boundary)
        up.clicked.connect(lambda: on_move(boundary, -1))
        down.clicked.connect(lambda: on_move(boundary, 1))
        remove.clicked.connect(lambda: on_remove(boundary))
        for button in (up, down, remove):
            button.setFixedHeight(20)
            button.setMinimumWidth(24)
            controls.addWidget(button)
        layout.addLayout(controls)


ROW_DRAG_MIME = "application/x-llama-test-builder-row"


class _DragGrip(QLabel):
    """Small drag handle on the left of a movable row; the only widget that starts a row drag."""

    def __init__(self, enabled: bool, start_drag, parent=None) -> None:
        super().__init__("⋮⋮", parent)
        self.setFixedWidth(16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setToolTip("Drag to reorder" if enabled else "The model row is always first")
        self.setEnabled(enabled)
        self._start_drag = start_drag
        self._pressed = None
        self._dragging = False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pressed = event.position()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            not self._dragging
            and self._pressed is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position() - self._pressed).manhattanLength() > QApplication.startDragDistance()
        ):
            self._dragging = True
            self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pressed = None
        super().mouseReleaseEvent(event)


class _RowDropHost(QWidget):
    """Drop target for the builder row list: shows the indicator, auto-scrolls, and routes drops to the builder."""

    def __init__(self, builder: "CommandBuilder", parent=None) -> None:
        super().__init__(parent)
        self._builder = builder
        self._drag_payload: dict | None = None
        self._last_position = None
        self._auto_scroll = QTimer(self)
        self._auto_scroll.setInterval(50)
        self._auto_scroll.timeout.connect(self._tick_auto_scroll)
        self.setAcceptDrops(True)

    @staticmethod
    def _parse_payload(mime_data: QMimeData) -> dict | None:
        if not mime_data.hasFormat(ROW_DRAG_MIME):
            return None
        try:
            payload = json.loads(bytes(mime_data.data(ROW_DRAG_MIME)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def dragEnterEvent(self, event) -> None:
        payload = self._parse_payload(event.mimeData())
        if payload is None or not self._builder.drop_target_for(payload, event.position()):
            event.ignore()
            return
        self._drag_payload = payload
        self._last_position = event.position()
        self._auto_scroll.start()
        event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if self._drag_payload is None or not self._builder.drop_target_for(self._drag_payload, event.position()):
            event.ignore()
            return
        self._last_position = event.position()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._end_drag_visuals()

    def dropEvent(self, event) -> None:
        payload = self._parse_payload(event.mimeData()) or self._drag_payload
        self._end_drag_visuals()
        if payload is None:
            event.ignore()
            return
        self._builder.perform_drop(payload, event.position())
        event.acceptProposedAction()

    def _end_drag_visuals(self) -> None:
        self._drag_payload = None
        self._auto_scroll.stop()
        self._builder.hide_drop_indicator()

    def _tick_auto_scroll(self) -> None:
        if self._last_position is None:
            self._auto_scroll.stop()
            return
        bar = self._builder.scroll_area.verticalScrollBar()
        top = bar.value()
        bottom = top + self._builder.scroll_area.viewport().height()
        if self._last_position.y() <= top + 24:
            bar.setValue(max(0, bar.value() - 12))
        elif self._last_position.y() >= bottom - 24:
            bar.setValue(min(bar.maximum(), bar.value() + 12))
