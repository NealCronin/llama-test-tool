import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QMimeData, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from app.models.flags import FlagSpec
from app.server import SERVER_COMMAND
from app.services.flag_catalog import FlagCatalog
from app.settings import AppSettings
from app.widgets.argument_row import ArgumentRow
from app.widgets.command_builder import ROW_DRAG_MIME, CommandBuilder, _DragGrip, _RowDropHost

app = QApplication.instance() or QApplication([])

FLAGS = ["-m", "-c", "-ngl", "--flash-attn", "--cache-type-k"]


def catalog() -> FlagCatalog:
    return FlagCatalog([
        FlagSpec("--model", ("-m", "--model"), "model", 1),
        FlagSpec("--ctx-size", ("-c", "--ctx-size"), "context", 1),
        FlagSpec("--n-gpu-layers", ("-ngl", "--n-gpu-layers"), "gpu layers", 1),
        FlagSpec("--flash-attn", ("--flash-attn",), "flash attention", 0),
        FlagSpec("--cache-type-k", ("--cache-type-k",), "cache type", 1),
    ])


def make_builder(spacers: list[int] | None = None):
    settings = AppSettings(last_command={
        "executable": SERVER_COMMAND,
        "arguments": [
            {"flag": "-m", "values": ["model.gguf"]},
            {"flag": "-c", "values": ["4096"]},
            {"flag": "-ngl", "values": ["99"]},
            {"flag": "--flash-attn", "values": []},
            {"flag": "--cache-type-k", "values": ["f16"]},
        ],
    })
    builder = CommandBuilder(settings, catalog())
    if spacers:
        settings.builder_spacers = list(spacers)
        builder.rebuild()
    return settings, builder


def show(builder) -> None:
    builder.show()
    builder.adjustSize()
    builder.arguments_host.adjustSize()
    app.processEvents()


def flags_of(builder) -> list[str]:
    return [argument.flag for argument in builder.command.arguments]


def row_point(builder, index: int, where: str) -> QPoint:
    """Host-local point in the top or bottom quarter of argument row `index` (or a spacer row position)."""
    element = builder.rows[index] if isinstance(index, int) else index
    top = element.mapTo(builder.arguments_host, QPoint(0, 0)).y()
    height = max(element.height(), 1)
    return QPoint(40, top + (max(1, height // 4) if where == "before" else height - max(1, height // 4)))


def mime_for(payload: dict) -> QMimeData:
    mime = QMimeData()
    mime.setData(ROW_DRAG_MIME, json.dumps(payload).encode("utf-8"))
    return mime


def drag_event(cls, point: QPoint, mime: QMimeData | None = None):
    mime = mime or QMimeData()
    return cls(point, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def mouse_event(event_type, point: QPointF, button: Qt.MouseButton, buttons: Qt.MouseButton):
    return QMouseEvent(event_type, point, point, point, button, buttons, Qt.KeyboardModifier.NoModifier)


# ------------------------------------------------------ argument reorder

def test_reorder_argument_moves_non_model_argument():
    _, builder = make_builder()
    assert builder.reorder_argument(2, 4) is True
    assert flags_of(builder) == ["-m", "-c", "--flash-attn", "--cache-type-k", "-ngl"]


def test_reorder_argument_rejects_model_source_and_invalid_targets():
    _, builder = make_builder()
    assert builder.reorder_argument(0, 3) is False      # the model argument never moves
    assert builder.reorder_argument(1, 0) is False      # nothing may land before the model row
    assert builder.reorder_argument(1, 5) is False      # out of range
    assert builder.reorder_argument(4, 4) is False      # same position
    assert flags_of(builder) == FLAGS


def test_grip_drop_after_row_moves_argument_end_to_end():
    _, builder = make_builder()
    show(builder)
    point = row_point(builder, 3, "after")  # land after the --flash-attn row
    assert builder.drop_target_for({"kind": "argument", "index": 2}, point) is True
    builder.perform_drop({"kind": "argument", "index": 2}, point)
    assert flags_of(builder) == ["-m", "-c", "--flash-attn", "-ngl", "--cache-type-k"]
    assert "-ngl" in builder.preview.text()


def test_grip_drop_before_row_inserts_at_that_position():
    _, builder = make_builder()
    show(builder)
    point = row_point(builder, 2, "before")  # land before the -ngl row
    assert builder.drop_target_for({"kind": "argument", "index": 4}, point) is True
    builder.perform_drop({"kind": "argument", "index": 4}, point)
    assert flags_of(builder) == ["-m", "-c", "--cache-type-k", "-ngl", "--flash-attn"]


def test_drop_after_last_row_moves_argument_to_end():
    _, builder = make_builder()
    show(builder)
    point = row_point(builder, 4, "after")  # below every row
    assert builder.drop_target_for({"kind": "argument", "index": 1}, point) is True
    builder.perform_drop({"kind": "argument", "index": 1}, point)
    assert flags_of(builder) == ["-m", "-ngl", "--flash-attn", "--cache-type-k", "-c"]


def test_drop_slots_adjacent_to_spacer_use_spacer_boundary():
    _, builder = make_builder(spacers=[2])
    show(builder)
    spacer = builder.spacer_rows[0]
    before = spacer.mapTo(builder.arguments_host, QPoint(0, 0))
    builder.perform_drop({"kind": "argument", "index": 4}, QPoint(40, before.y() + 1))  # slot 2
    assert flags_of(builder) == ["-m", "-c", "--cache-type-k", "-ngl", "--flash-attn"]
    _, builder = make_builder(spacers=[2])
    show(builder)
    spacer = builder.spacer_rows[0]
    before = spacer.mapTo(builder.arguments_host, QPoint(0, 0))
    builder.perform_drop({"kind": "argument", "index": 4}, QPoint(40, before.y() + spacer.height() - 1))  # slot 3
    assert flags_of(builder) == ["-m", "-c", "-ngl", "--cache-type-k", "--flash-attn"]


def test_model_row_cannot_be_dragged_or_dropped_onto():
    _, builder = make_builder()
    show(builder)
    payload = {"kind": "argument", "index": 0}
    assert builder.drop_target_for(payload, row_point(builder, 3, "after")) is False  # model as source
    assert builder.drop_target_for({"kind": "argument", "index": 1}, row_point(builder, 0, "before")) is False  # above model
    assert flags_of(builder) == FLAGS


def test_grip_widgets_mark_movable_rows_and_disable_model_row():
    _, builder = make_builder()
    show(builder)
    for index, row in enumerate(builder.rows):
        grip = row.layout().itemAt(0).widget()
        assert isinstance(grip, _DragGrip)
        if index == 0:
            assert grip.isEnabled() is False
            assert grip.toolTip() == "The model row is always first"
        else:
            assert grip.isEnabled() is True
            assert grip.cursor().shape() == Qt.CursorShape.OpenHandCursor
            assert grip.toolTip() == "Drag to reorder"


def test_drag_starts_only_after_grip_press_and_drag_distance():
    _, builder = make_builder()
    show(builder)
    calls: list[tuple] = []
    builder._start_row_drag = lambda row, kind, index: calls.append((row, kind, index))
    row = builder.rows[1]
    grip = row.layout().itemAt(0).widget()
    old_distance = app.startDragDistance()
    app.setStartDragDistance(4)
    try:
        grip.mousePressEvent(mouse_event(QEvent.Type.MouseButtonPress, QPointF(8, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        grip.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, QPointF(10, 9), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        assert calls == []  # inside the drag threshold: nothing starts
        grip.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, QPointF(20, 20), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        assert [kind for _, kind, _ in calls] == ["argument"] and [index for _, _, index in calls] == [1]
        grip.mouseReleaseEvent(mouse_event(QEvent.Type.MouseButtonRelease, QPointF(20, 20), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    finally:
        app.setStartDragDistance(old_distance)


def test_row_controls_do_not_start_drag():
    _, builder = make_builder()
    show(builder)
    calls: list[tuple] = []
    builder._start_row_drag = lambda row, kind, index: calls.append((row, kind, index))
    row = builder.rows[2]
    assert ArgumentRow.mouseMoveEvent is QWidget.mouseMoveEvent  # no drag wiring on the row itself
    old_distance = app.startDragDistance()
    app.setStartDragDistance(1)
    try:
        row.mousePressEvent(mouse_event(QEvent.Type.MouseButtonPress, QPointF(60, 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        row.mouseMoveEvent(mouse_event(QEvent.Type.MouseMove, QPointF(80, 40), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    finally:
        app.setStartDragDistance(old_distance)
    assert calls == []
    # a grip click without drag distance leaves the row's own controls fully usable
    grip = row.layout().itemAt(0).widget()
    grip.mousePressEvent(mouse_event(QEvent.Type.MouseButtonPress, QPointF(8, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
    grip.mouseReleaseEvent(mouse_event(QEvent.Type.MouseButtonRelease, QPointF(9, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
    assert calls == []
    row.down.click()
    assert flags_of(builder) == ["-m", "-c", "--flash-attn", "-ngl", "--cache-type-k"]


# ------------------------------------------------------ spacers

def test_spacer_drop_moves_boundary_to_target():
    _, builder = make_builder(spacers=[2])
    show(builder)
    assert builder.settings.builder_spacers == [2]
    point = row_point(builder, 3, "before")  # land before the -ngl row -> boundary 3
    assert builder.drop_target_for({"kind": "spacer", "index": 0}, point) is True
    builder.perform_drop({"kind": "spacer", "index": 0}, point)
    assert builder.settings.builder_spacers == [3]
    assert builder.visible_row_widgets.index(builder.spacer_rows[0]) == 3  # after the 3rd argument row


def test_spacer_drop_rejects_duplicate_and_out_of_range_boundaries():
    _, builder = make_builder(spacers=[2, 3])
    show(builder)
    payload = {"kind": "spacer", "index": 0}  # boundary 2
    assert builder.drop_target_for(payload, row_point(builder, 2, "before")) is False  # boundary 2 occupied
    assert builder.drop_target_for(payload, row_point(builder, 4, "after")) is False   # beyond last boundary
    assert builder.settings.builder_spacers == [2, 3]


def test_spacer_stays_presentation_only_for_rendered_command():
    _, plain = make_builder()
    _, builder = make_builder(spacers=[2])
    assert plain.command.rendered(vertical=False) == builder.command.rendered(vertical=False)
    assert "spacer" not in builder.command.rendered(vertical=False).lower()
    assert flags_of(builder) == FLAGS


def test_spacer_add_and_remove_boundaries():
    _, builder = make_builder()
    builder.add_spacer()
    assert builder.settings.builder_spacers == [4]  # first spacer lands between the last two arguments
    builder.add_spacer()
    assert builder.settings.builder_spacers == [3, 4]
    builder.remove_spacer(4)
    assert builder.settings.builder_spacers == [3]
    assert len(builder.spacer_rows) == 1


# ------------------------------------------------------ drop host behavior (offscreen)

def test_drop_host_accepts_valid_payload_and_reorders_on_drop():
    _, builder = make_builder()
    show(builder)
    host: _RowDropHost = builder.arguments_host
    point = row_point(builder, 3, "after")
    mime = mime_for({"kind": "argument", "index": 2})  # keep alive: the C++ drag events own a pointer to it
    host.dragEnterEvent(drag_event(QDragEnterEvent, point, mime))
    assert host._drag_payload == {"kind": "argument", "index": 2}
    assert builder._drop_indicator.parent() is not None  # insertion indicator visible
    host.dragMoveEvent(drag_event(QDragMoveEvent, point, mime))
    host.dropEvent(drag_event(QDropEvent, point, mime))
    assert flags_of(builder) == ["-m", "-c", "--flash-attn", "-ngl", "--cache-type-k"]
    assert builder._drop_indicator.parent() is None      # indicator cleared after drop
    assert host._drag_payload is None                    # auto-scroll armed state released


def test_drop_host_ignores_foreign_mime_and_invalid_payloads():
    _, builder = make_builder()
    show(builder)
    host = builder.arguments_host
    point = row_point(builder, 3, "after")
    foreign = QMimeData()
    foreign.setText("plain text")
    host.dragEnterEvent(drag_event(QDragEnterEvent, point, foreign))
    assert host._drag_payload is None and builder._drop_indicator.parent() is None
    broken = QMimeData()
    broken.setData(ROW_DRAG_MIME, b"{not json")
    host.dragEnterEvent(drag_event(QDragEnterEvent, point, broken))
    assert host._drag_payload is None
    model = mime_for({"kind": "argument", "index": 0})
    host.dragEnterEvent(drag_event(QDragEnterEvent, point, model))
    assert host._drag_payload is None  # model row is not draggable
    host.dragLeaveEvent(QDragLeaveEvent())
    assert host._drag_payload is None and host._auto_scroll.isActive() is False


def test_drop_host_drag_leave_hides_indicator():
    _, builder = make_builder()
    show(builder)
    host = builder.arguments_host
    point = row_point(builder, 3, "after")
    mime = mime_for({"kind": "argument", "index": 2})
    host.dragEnterEvent(drag_event(QDragEnterEvent, point, mime))
    assert builder._drop_indicator.parent() is not None
    host.dragLeaveEvent(QDragLeaveEvent())
    assert builder._drop_indicator.parent() is None


def test_indicator_tracks_valid_positions_and_model_top_is_invalid():
    _, builder = make_builder()
    show(builder)
    payload = {"kind": "argument", "index": 1}
    assert builder.drop_target_for(payload, row_point(builder, 4, "after")) is True
    assert builder._drop_indicator.parent() is not None
    assert builder.drop_target_for(payload, row_point(builder, 0, "before")) is False
    assert builder._drop_indicator.parent() is None
    builder.perform_drop(payload, row_point(builder, 4, "after"))
    assert builder._drop_indicator.parent() is None  # perform_drop always clears the indicator


def test_auto_scroll_ticks_inside_scroll_area_edges():
    _, builder = make_builder()
    show(builder)
    builder.scroll_area.setFixedHeight(100)
    builder.adjustSize()
    app.processEvents()
    bar = builder.scroll_area.verticalScrollBar()
    assert bar.maximum() > 0
    host = builder.arguments_host
    viewport = builder.scroll_area.viewport().height()
    host._last_position = QPoint(40, bar.value() + viewport - 10)  # near the bottom edge of the viewport
    before = bar.value()
    host._tick_auto_scroll()
    assert bar.value() == min(before + 12, bar.maximum())
    bar.setValue(bar.maximum())
    host._last_position = QPoint(40, bar.value() + 10)  # near the top edge
    before = bar.value()
    host._tick_auto_scroll()
    assert bar.value() == max(before - 12, 0)
    host._last_position = QPoint(40, bar.value() + 40)  # middle: no movement
    before = bar.value()
    host._tick_auto_scroll()
    assert bar.value() == before
    host._end_drag_visuals()
    assert host._auto_scroll.isActive() is False


def test_auto_scroll_timer_is_armed_on_enter_and_stopped_after_drop():
    _, builder = make_builder()
    show(builder)
    host = builder.arguments_host
    assert host._auto_scroll.isActive() is False
    point = row_point(builder, 3, "after")
    mime = mime_for({"kind": "argument", "index": 2})
    host.dragEnterEvent(drag_event(QDragEnterEvent, point, mime))
    assert host._auto_scroll.isActive() is True
    host.dropEvent(drag_event(QDropEvent, point, mime))
    assert host._auto_scroll.isActive() is False


# ------------------------------------------------------ server path

def test_builder_command_uses_fixed_release_server():
    _, builder = make_builder()
    assert builder.command.executable == SERVER_COMMAND
    assert SERVER_COMMAND == "Engines/llama.cpp/build/bin/Release/llama-server.exe"
    assert builder.command.rendered_lines().splitlines()[0] == SERVER_COMMAND


def test_imported_executable_is_normalized_to_fixed_server():
    settings = AppSettings(last_command={
        "executable": r"D:\Engines\other\llama-server.exe",
        "arguments": [{"flag": "-m", "values": ["model.gguf"]}],
    })
    builder = CommandBuilder(settings, catalog())
    assert builder.command.executable == SERVER_COMMAND
    assert builder.command.rendered_lines().splitlines()[0] == SERVER_COMMAND
