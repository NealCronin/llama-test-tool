"""Presence-aware visual editors for the advanced llama-swap configuration sections.

Each editor loads from the full round-trip mapping, applies only its own subtree, and
never writes a default that the user did not explicitly choose. Unknown fields at every
level survive; a failed validation leaves the file untouched.
"""
from __future__ import annotations

import re
from ruamel.yaml.comments import CommentedMap
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.llama_swap_service import LlamaSwapError, LlamaSwapService, _LOG_TIME_FORMATS, update_leaf
from app.widgets.ordered_list_editor import KeyValueTableEditor, OrderedSecretListEditor, OrderedStringListEditor
from app.widgets.optional_setting import OptionalBool, OptionalChoice, OptionalInt, OptionalSettingWidget
from app.widgets.structured_yaml import StructuredYamlEditor, parse_structured_yaml, structured_yaml_text

_MACRO_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_RESERVED_MACRO_NAMES = {"PID", "PORT", "MODEL_ID"}


def _presence_hint() -> QLabel:
    label = QLabel(
        "Fields display the effective default while unconfigured. Choose a value (or tick “Set”) to store it explicitly; "
        "saving back to the default removes any stored value. Reset to Default removes the section's keys entirely."
    )
    label.setWordWrap(True)
    label.setStyleSheet("color: #6b7280;")
    return label


class _SectionEditor(QWidget):
    """Base editor: one Save action plus a section-level Reset to Default."""

    saved = Signal(str)

    def __init__(self, section: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._section = section
        self._service: LlamaSwapService | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(8, 8, 8, 8)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        actions = QVBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        self.remove_button = QPushButton("Reset to Default (remove section)")
        self.remove_button.clicked.connect(self.reset_to_default)
        actions.addWidget(self.save_button)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.body.addWidget(_presence_hint())

    def load(self, data, service: LlamaSwapService) -> None:
        self._service = service
        self._load(data)

    def _load(self, data) -> None:  # pragma: no cover - overridden
        pass

    def apply(self, service: LlamaSwapService) -> None:
        """Perform the targeted write; raises LlamaSwapError/ValueError on failure."""
        with service.transaction(self._section) as data:
            self._apply(data)

    def save(self) -> None:
        try:
            self.apply(self._service)
        except (LlamaSwapError, ValueError) as error:
            QMessageBox.critical(self, "llama-swap", str(error))
            return
        self.saved.emit(f"Saved {self._section or 'settings'}; backup created.")

    def reset(self, service: LlamaSwapService) -> None:
        with service.transaction(self._section) as data:
            self._reset(data)

    def reset_to_default(self) -> None:
        try:
            self.reset(self._service)
        except (LlamaSwapError, ValueError) as error:
            QMessageBox.critical(self, "llama-swap", str(error))
            return
        self.saved.emit(f"Removed {self._section or 'settings'} keys; backup created.")

    def _apply(self, data) -> None:  # pragma: no cover - overridden
        pass

    def _reset(self, data) -> None:
        if self._section is None:
            raise LlamaSwapError("This editor manages multiple keys; reset its fields individually.")
        update_leaf(data, (self._section,), None)


    @staticmethod
    def _model_ids(data) -> list[str]:
        models = data.get("models") or {}
        return list(models)


class _MultiItemMixin:
    """Per-item draft model for list-based editors (profiles, selectors, routing groups, peers).

    An item's details are captured in ``self._drafts`` the first time the item is displayed and
    again whenever the selection moves away, so switching selection never destroys unsaved edits
    (Option B). Save writes every draft; entries the user never opened keep their on-disk data
    untouched. Drafts hold raw widget state so switching selection can never fail on half-typed
    values; validation runs when the draft is written.
    """

    _list: QListWidget

    def _multi_init(self) -> None:
        self._drafts: dict[str, dict] = {}
        self._displayed: str | None = None
        self._list.currentRowChanged.connect(self._multi_on_selection)

    def _multi_reset(self) -> None:
        self._drafts = {}
        self._displayed = None

    def _multi_ids(self) -> list[str]:
        return [self._list.item(index).text() for index in range(self._list.count())]

    @staticmethod
    def _same_sequence(current, desired) -> bool:
        """Order-aware comparison with str normalization for YAML scalars."""
        try:
            return [str(value) for value in current] == [str(value) for value in desired]
        except TypeError:
            return False

    def _multi_on_selection(self, row: int) -> None:
        if self._displayed is not None:
            self._drafts[self._displayed] = self._draft_from_widgets()
        if row < 0:
            self._displayed = None
            self._multi_clear_widgets()
            return
        item_id = self._list.item(row).text()
        draft = self._drafts.get(item_id)
        if draft is None:
            draft = self._draft_from_disk(self._multi_disk_entry(item_id))
            self._drafts[item_id] = draft
        self._draft_to_widgets(draft)
        self._displayed = item_id

    def _multi_stash(self) -> None:
        if self._displayed is not None:
            self._drafts[self._displayed] = self._draft_from_widgets()

    def _multi_drop(self, item_id: str) -> None:
        self._drafts.pop(item_id, None)
        if self._displayed == item_id:
            self._displayed = None

    def _multi_apply_map(self, data, section: str, path: tuple[str, ...] = ()) -> None:
        """Build the map at ``data[*path][section]`` in list order from the drafts.

        Entries no longer present in the list are removed; items that were never displayed are
        kept exactly as they appear on disk. Subclasses validate and fill each entry.
        """
        from ruamel.yaml.comments import CommentedMap

        def find_parent(parent):
            for key in path:
                node = parent.get(key)
                if not isinstance(node, (CommentedMap, dict)):
                    return None
                parent = node
            return parent

        if not self._list.count():
            parent = find_parent(data)
            if parent is not None:
                parent.pop(section, None)
            return
        parent = data
        for key in path:
            node = parent.get(key)
            if not isinstance(node, (CommentedMap, dict)):
                node = CommentedMap()
                parent[key] = node
            parent = node
        section_map = parent.get(section)
        if not isinstance(section_map, (CommentedMap, dict)):
            section_map = CommentedMap()
            parent[section] = section_map
        for key in list(section_map):
            if str(key) not in self._multi_ids():
                section_map.pop(key)
        for item_id in self._multi_ids():
            entry = section_map.get(item_id)
            if not isinstance(entry, (CommentedMap, dict)):
                entry = CommentedMap()
                section_map[item_id] = entry
            draft = self._drafts.get(item_id)
            if draft is None:
                continue
            self._multi_validate_draft(item_id, draft)
            self._multi_write_entry(entry, draft)

    # Per-editor hooks
    def _multi_disk_entry(self, item_id: str) -> dict:  # pragma: no cover - overridden
        data = self._service.load()
        return (data.get(self._section) or {}).get(item_id) or {}

    def _draft_from_disk(self, entry: dict) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def _draft_to_widgets(self, draft: dict) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _draft_from_widgets(self) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def _multi_clear_widgets(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _multi_validate_draft(self, item_id: str, draft: dict) -> None:  # pragma: no cover - overridden
        pass

    def _multi_write_entry(self, entry, draft: dict) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class GeneralSettingsEditor(_SectionEditor):
    MANAGED = ("healthCheckTimeout", "globalTTL", "unloadTimeout", "startPort", "sendLoadingState", "includeAliasesInList", "logRequests")

    def __init__(self, parent=None) -> None:
        super().__init__(None, parent)
        self.remove_button.setText("Reset to Defaults (remove stored keys)")
        self.health_check = OptionalInt(120)
        self.global_ttl = OptionalInt(0)
        self.unload = OptionalInt(10)
        self.start_port = OptionalInt(5800)
        self.send_loading = OptionalBool(False)
        self.include_aliases = OptionalBool(False)
        self.log_requests = OptionalBool(False)
        form = QFormLayout()
        form.addRow("Health check timeout (seconds)", self.health_check)
        form.addRow("Global TTL (seconds)", self.global_ttl)
        form.addRow("Unload timeout (seconds)", self.unload)
        form.addRow("Start port", self.start_port)
        form.addRow("Send loading state", self.send_loading)
        form.addRow("Include aliases in list", self.include_aliases)
        form.addRow("Log requests", self.log_requests)
        self.body.addLayout(form)
        self._widgets = {
            "healthCheckTimeout": self.health_check, "globalTTL": self.global_ttl, "unloadTimeout": self.unload,
            "startPort": self.start_port, "sendLoadingState": self.send_loading, "includeAliasesInList": self.include_aliases,
            "logRequests": self.log_requests,
        }

    def _load(self, data) -> None:
        for key, widget in self._widgets.items():
            widget.load(key in data, data.get(key))

    def _reset(self, data) -> None:
        for key in self.MANAGED:
            data.pop(key, None)

    def _apply(self, data) -> None:
        for key, widget in self._widgets.items():
            value = widget.explicit()
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value


class LoggingSettingsEditor(_SectionEditor):
    MANAGED = ("logLevel", "logTimeFormat", "logToStdout")

    def __init__(self, parent=None) -> None:
        super().__init__(None, parent)
        self.remove_button.setText("Reset to Defaults (remove stored keys)")
        self.log_level = OptionalChoice(("debug", "info", "warn", "error"), "info")
        self.log_time_format = OptionalChoice(_LOG_TIME_FORMATS, "")
        self.log_output = OptionalChoice(("proxy", "upstream", "both", "none"), "proxy")
        form = QFormLayout()
        form.addRow("Log level", self.log_level)
        form.addRow("Log time format", self.log_time_format)
        form.addRow("Log to stdout", self.log_output)
        self.body.addLayout(form)
        self._widgets = {"logLevel": self.log_level, "logTimeFormat": self.log_time_format, "logToStdout": self.log_output}

    def _load(self, data) -> None:
        for key, widget in self._widgets.items():
            widget.load(key in data, data.get(key))

    def _reset(self, data) -> None:
        for key in self.MANAGED:
            data.pop(key, None)

    def _apply(self, data) -> None:
        for key, widget in self._widgets.items():
            value = widget.explicit()
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value


class ActivityPerformanceEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("ui", parent)
        self.metrics = OptionalInt(1000)
        self.capture = OptionalInt(5)
        self.store_path = OptionalSettingWidget("", placeholder="Path to the llama-swap store directory")
        self.perf_disabled = OptionalBool(False)
        self.perf_every = OptionalSettingWidget("15s", placeholder="15s, 1m, …")
        self.session_id = OrderedStringListEditor(("X-Session-ID", "X-Litellm-Session-Id"))
        metrics_group = QGroupBox("Activity")
        metrics_form = QFormLayout(metrics_group)
        metrics_form.addRow("Metrics max in memory", self.metrics)
        metrics_form.addRow("Capture buffer", self.capture)
        store_group = QGroupBox("Persistent store")
        store_form = QFormLayout(store_group)
        store_form.addRow("Path", self.store_path)
        perf_group = QGroupBox("Performance monitoring")
        perf_form = QFormLayout(perf_group)
        perf_form.addRow("Disabled", self.perf_disabled)
        perf_form.addRow("Every", self.perf_every)
        ui_group = QGroupBox("UI activity session headers")
        ui_layout = QVBoxLayout(ui_group)
        self.ui_configured = QCheckBox("Configure session headers (unchecked = runtime defaults)", ui_group)
        ui_layout.addWidget(self.ui_configured)
        ui_layout.addWidget(self.session_id)
        self.body.addWidget(metrics_group)
        self.body.addWidget(store_group)
        self.body.addWidget(perf_group)
        self.body.addWidget(ui_group)

    def _load(self, data) -> None:
        self.metrics.load("metricsMaxInMemory" in data, data.get("metricsMaxInMemory", 1000))
        self.capture.load("captureBuffer" in data, data.get("captureBuffer", 5))
        store = data.get("store") or {}
        self.store_path.load("path" in store, store.get("path", ""))
        performance = data.get("performance") or {}
        self.perf_disabled.load("disabled" in performance, performance.get("disabled", False))
        self.perf_every.load("every" in performance, performance.get("every", "15s"))
        ui = ((data.get("ui") or {}).get("activity") or {})
        self.ui_configured.setChecked("session_id" in ui)
        self.session_id.set_values(ui.get("session_id") or ("X-Session-ID", "X-Litellm-Session-Id"))

    def _reset(self, data) -> None:
        data.pop("metricsMaxInMemory", None)
        data.pop("captureBuffer", None)
        update_leaf(data, ("store",), None)
        update_leaf(data, ("performance",), None)
        update_leaf(data, ("ui",), None)

    def _apply(self, data) -> None:
        for key, widget in (("metricsMaxInMemory", self.metrics), ("captureBuffer", self.capture)):
            value = widget.explicit()
            data.pop(key, None) if value is None else data.__setitem__(key, value)
        store_path = self.store_path.explicit()
        update_leaf(data, ("store", "path"), store_path if store_path else None)
        disabled = self.perf_disabled.explicit()
        update_leaf(data, ("performance", "disabled"), disabled)
        every = self.perf_every.explicit()
        update_leaf(data, ("performance", "every"), every if every else None)
        if self.ui_configured.isChecked():
            update_leaf(data, ("ui", "activity", "session_id"), self.session_id.values() or None)
        else:
            update_leaf(data, ("ui", "activity", "session_id"), None)


class SecurityEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("apiKeys", parent)
        self.keys = OrderedSecretListEditor(self)
        hint = QLabel("Keys are masked in the list. Values may be literal keys or ${env.NAME} references.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280;")
        self.body.addWidget(hint)
        self.body.addWidget(self.keys, 1)

    def _load(self, data) -> None:
        self.keys.set_values(data.get("apiKeys") or [])

    def _apply(self, data) -> None:
        update_leaf(data, ("apiKeys",), self.keys.values() or None)


class MacrosEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("macros", parent)
        self.rows: list[_MacroRow] = []
        self.rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addStretch()
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.rows_widget)
        buttons = QVBoxLayout()
        self.add_button = QPushButton("Add Macro")
        self.add_button.clicked.connect(lambda: self._add_row("", "", "string"))
        self.remove_button_row = QPushButton("Remove Selected")
        self.remove_button_row.clicked.connect(self._remove_row)
        self.up_button = QPushButton("Move Selected Up")
        self.up_button.clicked.connect(lambda: self._move_row(-1))
        self.down_button = QPushButton("Move Selected Down")
        self.down_button.clicked.connect(lambda: self._move_row(1))
        for button in (self.add_button, self.remove_button_row, self.up_button, self.down_button):
            buttons.addWidget(button)
        note = QLabel("Order matters: macros are expanded in listed order. Names must be unique and cannot use PID, PORT, or MODEL_ID.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280;")
        buttons.addWidget(note)
        buttons.addStretch()
        self.body.addWidget(scroll, 1)
        self.body.addLayout(buttons)

    def _add_row(self, name: str, value: str, mtype: str, select: bool = True) -> _MacroRow:
        row = _MacroRow(name, value, mtype)
        self.rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        if select:
            row.select()
        return row

    def _remove_row(self) -> None:
        for index, row in enumerate(self.rows):
            if row.has_focus():
                del self.rows[index]
                self.rows_layout.removeWidget(row)
                row.deleteLater()
                return

    def _move_row(self, offset: int) -> None:
        for index, row in enumerate(self.rows):
            if row.has_focus():
                target = index + offset
                if not 0 <= target < len(self.rows):
                    return
                self.rows[index], self.rows[target] = self.rows[target], self.rows[index]
                self.rows_layout.insertWidget(target, self.rows[target])
                return

    def _load(self, data) -> None:
        for row in list(self.rows):
            self.rows.remove(row)
            self.rows_layout.removeWidget(row)
            row.deleteLater()
        macros = data.get("macros") or {}
        for name, value in macros.items():
            if isinstance(value, bool):
                mtype, text = "boolean", "true" if value else "false"
            elif isinstance(value, int):
                mtype, text = "integer", str(value)
            elif isinstance(value, float):
                mtype, text = "float", str(value)
            else:
                mtype, text = "string", str(value)
            self._add_row(str(name), text, mtype, select=False)

    def _apply(self, data) -> None:
        macros: dict[str, object] = {}
        for row in self.rows:
            name, value = row.parsed()
            if name in _RESERVED_MACRO_NAMES:
                raise ValueError(f"Macro name {name!r} is reserved by llama-swap.")
            if name in macros:
                raise ValueError(f"Macro name {name!r} is listed more than once.")
            macros[name] = value
        update_leaf(data, ("macros",), macros or None)


class _MacroRow(QWidget):
    def __init__(self, name: str = "", value: str = "", mtype: str = "string") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("Name (letters, digits, _ or -)")
        self.type_combo = QComboBox(self)
        self.type_combo.addItems(("string", "integer", "float", "boolean"))
        self.value_edit = QLineEdit(self)
        self.value_edit.setPlaceholderText("Value")
        top = QVBoxLayout()
        top.addWidget(self.name_edit)
        top.addWidget(self.value_edit)
        layout.addLayout(top)
        layout.addWidget(self.type_combo)
        self.name_edit.textChanged.connect(lambda: self.select())
        self.value_edit.textChanged.connect(lambda: self.select())
        self.name_edit.setText(name)
        self.type_combo.setCurrentText(mtype)
        self.value_edit.setText(value)

    def select(self) -> None:
        self.name_edit.setFocus()

    def has_focus(self) -> bool:
        return self.name_edit.hasFocus() or self.value_edit.hasFocus()

    def parsed(self) -> tuple[str, object]:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Every macro needs a name.")
        if not _MACRO_NAME.match(name):
            raise ValueError(f"Macro name {name!r} must contain only letters, digits, underscore, or dash.")
        text = self.value_edit.text()
        mtype = self.type_combo.currentText()
        if mtype == "string":
            return name, text
        if mtype == "integer":
            try:
                return name, int(text)
            except ValueError as error:
                raise ValueError(f"Macro {name!r} has a non-integer value: {text!r}.") from error
        if mtype == "float":
            try:
                return name, float(text)
            except ValueError as error:
                raise ValueError(f"Macro {name!r} has a non-numeric value: {text!r}.") from error
        lowered = text.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return name, True
        if lowered in {"false", "0", "no"}:
            return name, False
        raise ValueError(f"Macro {name!r} has an invalid boolean value: {text!r}.")


class HooksEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("hooks", parent)
        self.preload = OrderedStringListEditor(placeholder="Model ID to preload on startup", use_combo=True)
        hint = QLabel("Models listed here are loaded when llama-swap starts. Order is preserved.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280;")
        self.body.addWidget(hint)
        self.body.addWidget(self.preload, 1)

    def _load(self, data) -> None:
        preload = (((data.get("hooks") or {}).get("on_startup") or {}).get("preload") or [])
        self.preload.set_values(preload)
        self.preload.set_choices(self._model_ids(data))

    def _apply(self, data) -> None:
        update_leaf(data, ("hooks", "on_startup", "preload"), self.preload.values() or None)


class UpstreamEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("upstream", parent)
        self.ignore_paths = OrderedStringListEditor((), placeholder="Regex, e.g. ^/v1/completions$")
        hint = QLabel("Request paths matching these regexes bypass llama-swap and go straight to the upstream proxy.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280;")
        self.body.addWidget(hint)
        self.body.addWidget(self.ignore_paths, 1)

    def _load(self, data) -> None:
        self.ignore_paths.set_values((data.get("upstream") or {}).get("ignorePaths") or [])

    def _apply(self, data) -> None:
        update_leaf(data, ("upstream", "ignorePaths"), self.ignore_paths.values() or None)


class ProfilesEditor(_MultiItemMixin, _SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("profiles", parent)
        self._list = self.list = QListWidget(self)
        self.description = QLineEdit(self)
        self.pins = KeyValueTableEditor(("Source ID", "Target ID (blank = disabled)"), self)
        add = QPushButton("Add Profile")
        add.clicked.connect(self._add_profile)
        remove = QPushButton("Remove Profile")
        remove.clicked.connect(self._remove_profile)
        row = QVBoxLayout()
        row.addWidget(self.list, 1)
        form = QFormLayout()
        form.addRow("Description", self.description)
        form.addRow("Pins", self.pins)
        form.addRow(add)
        form.addRow(remove)
        row.addLayout(form)
        self.body.addLayout(row)
        self._multi_init()

    def _validate_new_profile_id(self, profile_id: str) -> None:
        """Bundled-schema check: profile IDs are any non-empty string (propertyNames minLength 1)."""
        profile_id = profile_id.strip()
        if not profile_id:
            raise ValueError("Profile ID cannot be empty.")
        if profile_id in self._multi_ids():
            raise ValueError(f"A profile with ID {profile_id!r} already exists.")

    def _add_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Profile", "Profile ID (any non-empty string):")
        if not ok:
            return
        try:
            self._validate_new_profile_id(name)
        except ValueError as error:
            QMessageBox.warning(self, "Profiles", str(error))
            return
        self.list.addItem(QListWidgetItem(name.strip()))
        self.list.setCurrentItem(self.list.item(self.list.count() - 1))

    def _remove_profile(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._multi_drop(item.text())
            self.list.takeItem(self.list.row(item))

    def _load(self, data) -> None:
        self._multi_reset()
        self.list.clear()
        for profile_id in (data.get("profiles") or {}):
            self.list.addItem(QListWidgetItem(str(profile_id)))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._multi_clear_widgets()

    def _apply(self, data) -> None:
        self._multi_stash()
        self._multi_apply_map(data, "profiles")

    def _draft_from_disk(self, entry: dict) -> dict:
        pins = entry.get("pins") or {}
        return {
            "description": str(entry.get("description", "")),
            "pins": [(str(source), "" if target is None else str(target)) for source, target in pins.items()],
        }

    def _draft_to_widgets(self, draft: dict) -> None:
        self.description.setText(draft["description"])
        self.pins.set_items(draft["pins"])

    def _draft_from_widgets(self) -> dict:
        return {
            "description": self.description.text(),
            "pins": self.pins.items(),
        }

    def _multi_clear_widgets(self) -> None:
        self.description.clear()
        self.pins.set_items([])

    def _multi_validate_draft(self, profile_id: str, draft: dict) -> None:
        pins = [(source.strip(), target.strip()) for source, target in draft["pins"] if source.strip() or target.strip()]
        draft["pins"] = pins
        if not pins:
            raise ValueError(f"Profile {profile_id!r} needs at least one pin.")
        for source, _target in pins:
            if not source:
                raise ValueError(f"Profile {profile_id!r} has a pin without a source model ID; a blank target disables the pin.")

    def _multi_write_entry(self, entry, draft: dict) -> None:
        description = draft["description"].strip()
        if description:
            if str(entry.get("description", "")) != description:
                entry["description"] = description
        elif "description" in entry:
            entry.pop("description")
        desired = [(source, (target if target else None)) for source, target in draft["pins"]]
        current = [(str(key), (None if value is None else str(value))) for key, value in (entry.get("pins") or {}).items()]
        if current != desired:
            pins = CommentedMap()
            for source, target in desired:
                pins[source] = target
            entry["pins"] = pins


class SelectorsEditor(_MultiItemMixin, _SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("selectors", parent)
        self._list = self.list = QListWidget(self)
        self.strategy = QComboBox(self)
        self.strategy.addItems(("warm", "pin", "spillover"))
        self.targets = OrderedStringListEditor(placeholder="Model, alias, or peer model ID", use_combo=True)
        self.name = QLineEdit(self)
        self.description = QLineEdit(self)
        self.unlisted = QCheckBox("Unlisted", self)
        self.spillover = OptionalInt(1)
        self.metadata = StructuredYamlEditor("Optional metadata (YAML mapping)", self)
        add = QPushButton("Add Selector")
        add.clicked.connect(self._add_selector)
        remove = QPushButton("Remove Selector")
        remove.clicked.connect(self._remove_selector)
        row = QVBoxLayout()
        row.addWidget(self.list, 1)
        form = QFormLayout()
        form.addRow("Strategy", self.strategy)
        form.addRow("Targets", self.targets)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow(self.unlisted)
        form.addRow("Spillover (spillover strategy)", self.spillover)
        form.addRow("Metadata", self.metadata)
        form.addRow(add)
        form.addRow(remove)
        row.addLayout(form)
        self.body.addLayout(row)
        self.strategy.currentTextChanged.connect(lambda text: self.spillover.edit.setEnabled(text == "spillover"))
        self._multi_init()

    def _add_selector(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Selector", "Selector ID:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._multi_ids():
            QMessageBox.warning(self, "Selectors", "A selector with that ID already exists.")
            return
        self.list.addItem(QListWidgetItem(name))
        self.list.setCurrentItem(self.list.item(self.list.count() - 1))

    def _remove_selector(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._multi_drop(item.text())
            self.list.takeItem(self.list.row(item))

    def _load(self, data) -> None:
        self._multi_reset()
        self.list.clear()
        for selector_id in (data.get("selectors") or {}):
            self.list.addItem(QListWidgetItem(str(selector_id)))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._multi_clear_widgets()

    def _apply(self, data) -> None:
        self._multi_stash()
        self._multi_apply_map(data, "selectors")

    def _draft_from_disk(self, entry: dict) -> dict:
        settings = entry.get("settings") or {}
        return {
            "strategy": str(entry.get("strategy", "warm")),
            "targets": [str(target) for target in (entry.get("targets") or [])],
            "name": str(entry.get("name", "")),
            "description": str(entry.get("description", "")),
            "unlisted": bool(entry.get("unlisted", False)),
            "spillover": str(settings["spillover"]) if settings and "spillover" in settings else None,
            "metadata": structured_yaml_text(entry.get("metadata")),
        }

    def _draft_to_widgets(self, draft: dict) -> None:
        self.strategy.setCurrentText(draft["strategy"])
        if draft["strategy"] == "spillover" and draft["spillover"] is not None:
            self.spillover.load(True, draft["spillover"])
        else:
            self.spillover.load(False, 1)
        self.targets.set_values(draft["targets"])
        self.name.setText(draft["name"])
        self.description.setText(draft["description"])
        self.unlisted.setChecked(draft["unlisted"])
        self.metadata.set_text(draft["metadata"])

    def _draft_from_widgets(self) -> dict:
        return {
            "strategy": self.strategy.currentText(),
            "targets": self.targets.values(),
            "name": self.name.text(),
            "description": self.description.text(),
            "unlisted": self.unlisted.isChecked(),
            "spillover": self.spillover.explicit_raw(),
            "metadata": self.metadata.raw(),
        }

    def _multi_clear_widgets(self) -> None:
        self.strategy.setCurrentText("warm")
        self.targets.set_values([])
        self.name.clear()
        self.description.clear()
        self.unlisted.setChecked(False)
        self.spillover.load(False, 1)
        self.metadata.set_object(None)

    def _multi_validate_draft(self, selector_id: str, draft: dict) -> None:
        draft["targets"] = [str(target).strip() for target in draft["targets"] if str(target).strip()]
        if not draft["targets"]:
            raise ValueError(f"Selector {selector_id!r} needs at least one target.")
        if draft["strategy"] == "spillover":
            raw = draft["spillover"]
            if raw is None:
                raise ValueError(f"Selector {selector_id!r} (spillover) needs a spillover count.")
            try:
                value = int(raw)
            except ValueError as error:
                raise ValueError(f"Selector {selector_id!r} (spillover) needs an integer spillover count.") from error
            if value < 1:
                raise ValueError(f"Selector {selector_id!r} (spillover) needs a spillover count of at least 1.")
            draft["spillover"] = str(value)
        else:
            draft["spillover"] = None
        if draft["metadata"]:
            parse_structured_yaml(draft["metadata"])

    def _multi_write_entry(self, entry, draft: dict) -> None:
        if str(entry.get("strategy", "warm")) != draft["strategy"]:
            entry["strategy"] = draft["strategy"]
        if not self._same_sequence(entry.get("targets") or [], draft["targets"]):
            entry["targets"] = list(draft["targets"])
        name = draft["name"].strip()
        if name:
            if str(entry.get("name", "")) != name:
                entry["name"] = name
        elif "name" in entry:
            entry.pop("name")
        description = draft["description"].strip()
        if description:
            if str(entry.get("description", "")) != description:
                entry["description"] = description
        elif "description" in entry:
            entry.pop("description")
        if draft["unlisted"]:
            if entry.get("unlisted") is not True:
                entry["unlisted"] = True
        elif entry.get("unlisted") is True:
            entry.pop("unlisted")
        settings = entry.get("settings")
        if draft["strategy"] == "spillover":
            if not isinstance(settings, (CommentedMap, dict)):
                settings = CommentedMap()
                entry["settings"] = settings
            if draft["spillover"] is None:
                if "spillover" in settings:
                    settings.pop("spillover")
            else:
                value = int(draft["spillover"])
                if settings.get("spillover") != value:
                    settings["spillover"] = value
        elif isinstance(settings, (CommentedMap, dict)) and "spillover" in settings:
            settings.pop("spillover")
            if not settings:
                entry.pop("settings")
        metadata = parse_structured_yaml(draft["metadata"])
        if metadata:
            if entry.get("metadata") != metadata:
                entry["metadata"] = metadata
        else:
            entry.pop("metadata", None)

class RoutingEditor(_MultiItemMixin, _SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("routing", parent)
        self.legacy_banner = QLabel("")
        self.legacy_banner.setWordWrap(True)
        self.legacy_banner.setStyleSheet("color: #92400e; background: #fef3c7; padding: 4px;")
        self.legacy_banner.setVisible(False)
        self.body.addWidget(self.legacy_banner)

        scheduler_group = QGroupBox("Scheduler")
        scheduler_form = QFormLayout(scheduler_group)
        self.scheduler_use = QComboBox(scheduler_group)
        self.scheduler_use.addItem("fifo")
        scheduler_form.addRow("Scheduler", self.scheduler_use)
        self.priority = KeyValueTableEditor(("Model ID", "Priority (integer)"), scheduler_group)
        scheduler_form.addRow("FIFO priority", self.priority)
        self.body.addWidget(scheduler_group)

        router_group = QGroupBox("Router")
        router_form = QFormLayout(router_group)
        self.router_use = QComboBox(router_group)
        self.router_use.addItem("(none)", None)
        self.router_use.addItem("group", "group")
        self.router_use.addItem("matrix", "matrix")
        router_form.addRow("Router", self.router_use)

        self.groups_box = QGroupBox("Group routing", router_group)
        self.groups_layout = QVBoxLayout(self.groups_box)
        self._list = self.groups_list = QListWidget(self.groups_box)
        self.group_swap = OptionalBool(True, self.groups_box)
        self.group_exclusive = OptionalBool(True, self.groups_box)
        self.group_persistent = OptionalBool(False, self.groups_box)
        self.group_members = OrderedStringListEditor(placeholder="Model ID", use_combo=True, parent=self.groups_box)
        group_add = QPushButton("Add Group", self.groups_box)
        group_add.clicked.connect(self._add_group)
        group_remove = QPushButton("Remove Group", self.groups_box)
        group_remove.clicked.connect(self._remove_group)
        self.groups_layout.addWidget(self.groups_list)
        self.groups_layout.addWidget(self.group_swap)
        self.groups_layout.addWidget(self.group_exclusive)
        self.groups_layout.addWidget(self.group_persistent)
        self.groups_layout.addWidget(self.group_members)
        self.groups_layout.addWidget(group_add)
        self.groups_layout.addWidget(group_remove)
        router_form.addRow(self.groups_box)

        self.matrix_box = QGroupBox("Matrix routing", router_group)
        matrix_layout = QVBoxLayout(self.matrix_box)
        self.matrix_vars = KeyValueTableEditor(("Var (name)", "Model ID"), self.matrix_box)
        self.matrix_costs = KeyValueTableEditor(("Var or model ID", "Eviction cost (integer)"), self.matrix_box)
        self.matrix_sets = StructuredYamlEditor("Sets: mapping of set name → expression (operators & | () and +ref)", self.matrix_box)
        matrix_layout.addWidget(self.matrix_vars)
        matrix_layout.addWidget(self.matrix_costs)
        matrix_layout.addWidget(self.matrix_sets)
        router_form.addRow(self.matrix_box)
        self.body.addWidget(router_group)

        self._models: list[str] = []
        self._multi_init()
        self.router_use.currentIndexChanged.connect(self._router_mode_changed)
        self._router_mode_changed()

    def _router_mode_changed(self) -> None:
        mode = self.router_use.currentData()
        self.groups_box.setVisible(mode == "group")
        self.matrix_box.setVisible(mode == "matrix")

    def _add_group(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Group", "Group name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._multi_ids():
            QMessageBox.warning(self, "Routing", "A group with that name already exists.")
            return
        self.groups_list.addItem(QListWidgetItem(name))
        self.groups_list.setCurrentItem(self.groups_list.item(self.groups_list.count() - 1))

    def _remove_group(self) -> None:
        item = self.groups_list.currentItem()
        if item is not None:
            self._multi_drop(item.text())
            self.groups_list.takeItem(self.groups_list.row(item))

    def _multi_disk_entry(self, group_name: str) -> dict:
        data = self._service.load()
        groups = (((data.get("routing") or {}).get("router") or {}).get("settings") or {}).get("groups") or {}
        group = groups.get(group_name)
        return group if isinstance(group, dict) else {}

    def _draft_from_disk(self, entry: dict) -> dict:
        return {
            "swap": entry.get("swap") if "swap" in entry else None,
            "exclusive": entry.get("exclusive") if "exclusive" in entry else None,
            "persistent": entry.get("persistent") if "persistent" in entry else None,
            "members": [str(member) for member in (entry.get("members") or [])],
        }

    def _draft_to_widgets(self, draft: dict) -> None:
        self.group_swap.load(draft["swap"] is not None, draft["swap"] if draft["swap"] is not None else True)
        self.group_exclusive.load(draft["exclusive"] is not None, draft["exclusive"] if draft["exclusive"] is not None else True)
        self.group_persistent.load(draft["persistent"] is not None, draft["persistent"] if draft["persistent"] is not None else False)
        self.group_members.set_values(draft["members"])
        self.group_members.set_choices(self._models)

    def _draft_from_widgets(self) -> dict:
        return {
            "swap": self.group_swap.explicit(),
            "exclusive": self.group_exclusive.explicit(),
            "persistent": self.group_persistent.explicit(),
            "members": self.group_members.values(),
        }

    def _multi_clear_widgets(self) -> None:
        self.group_swap.reset()
        self.group_exclusive.reset()
        self.group_persistent.reset()
        self.group_members.set_values([])

    def _multi_validate_draft(self, group_name: str, draft: dict) -> None:
        draft["members"] = [str(member).strip() for member in draft["members"] if str(member).strip()]
        if not draft["members"]:
            raise ValueError(f"Group {group_name!r} needs at least one member.")

    def _multi_write_entry(self, entry, draft: dict) -> None:
        for key in ("swap", "exclusive", "persistent"):
            value = draft[key]
            if value is None:
                if key in entry:
                    entry.pop(key)
            elif bool(entry.get(key)) is not value:
                entry[key] = value
        if not self._same_sequence(entry.get("members") or [], draft["members"]):
            entry["members"] = list(draft["members"])

    def _load(self, data) -> None:
        legacy = [name for name in ("groups", "matrix") if name in data]
        self.legacy_banner.setVisible(bool(legacy))
        if legacy:
            self.legacy_banner.setText(
                f"Legacy top-level {legacy[0]!r} section detected. It is preserved untouched; llama-swap does not accept "
                "legacy top-level groups/matrix together with routing.router, so saves are blocked until the legacy "
                "section is removed or routing.router is not configured."
            )
        routing = data.get("routing") or {}
        scheduler = routing.get("scheduler") or {}
        priority = ((scheduler.get("settings") or {}).get("fifo") or {}).get("priority") or {}
        self.priority.set_items([(str(model), str(value)) for model, value in priority.items()])
        router = routing.get("router") or {}
        use = router.get("use")
        self.router_use.setCurrentIndex(self.router_use.findData(use) if use else 0)
        self._models = self._model_ids(data)
        self._multi_reset()
        self.groups_list.clear()
        groups = (router.get("settings") or {}).get("groups") or {}
        for group_name in groups:
            self.groups_list.addItem(QListWidgetItem(str(group_name)))
        if self.groups_list.count():
            self.groups_list.setCurrentRow(0)
        else:
            self._multi_clear_widgets()
        settings = router.get("settings") or {}
        matrix = settings.get("matrix") or {}
        self.matrix_vars.set_items([(str(var), str(model)) for var, model in (matrix.get("vars") or {}).items()])
        self.matrix_costs.set_items([(str(var), str(cost)) for var, cost in (matrix.get("evict_costs") or {}).items()])
        self.matrix_sets.set_object(matrix.get("sets"))
        self.group_members.set_choices(self._models)

    def _apply(self, data) -> None:
        from ruamel.yaml.comments import CommentedMap

        existing = data.get("routing")
        if not isinstance(existing, (CommentedMap, dict)):
            existing = CommentedMap()
            data["routing"] = existing
        routing = existing

        priority_rows = self.priority.items()
        if priority_rows:
            priority = CommentedMap()
            for model, text in priority_rows:
                if not model.strip():
                    raise ValueError("Priority rows need a model ID.")
                try:
                    value = int(text)
                except ValueError as error:
                    raise ValueError(f"Priority for {model!r} must be an integer.") from error
                priority[model.strip()] = value
            fifo = CommentedMap()
            fifo["priority"] = priority
            scheduler_settings = CommentedMap()
            scheduler_settings["fifo"] = fifo
            scheduler = CommentedMap()
            scheduler["use"] = "fifo"
            scheduler["settings"] = scheduler_settings
            routing["scheduler"] = scheduler
        else:
            routing.pop("scheduler", None)

        use = self.router_use.currentData()
        router = routing.get("router")
        if not isinstance(router, (CommentedMap, dict)):
            router = CommentedMap()
            routing["router"] = router
        if use == "group":
            if not self.groups_list.count():
                raise ValueError("Group routing needs at least one group.")
            self._multi_stash()
            self._multi_apply_map(routing, "groups", path=("router", "settings"))
            router["use"] = "group"
            # The inactive engine's settings (if any) are preserved untouched;
            # `use` selects the active one at runtime.
        elif use == "matrix":
            matrix = CommentedMap()
            vars_rows = self.matrix_vars.items()
            if vars_rows:
                matrix_vars = CommentedMap()
                for var, model in vars_rows:
                    if not var.strip():
                        raise ValueError("Matrix vars need a variable name.")
                    matrix_vars[var.strip()] = model.strip()
                matrix["vars"] = matrix_vars
            cost_rows = self.matrix_costs.items()
            if cost_rows:
                costs = CommentedMap()
                for var, text in cost_rows:
                    if not var.strip():
                        raise ValueError("Matrix eviction costs need a variable or model ID.")
                    try:
                        costs[var.strip()] = int(text)
                    except ValueError as error:
                        raise ValueError(f"Eviction cost for {var!r} must be an integer.") from error
                matrix["evict_costs"] = costs
            sets = self.matrix_sets.object()
            if "vars" not in matrix:
                raise ValueError("Matrix routing needs at least one var (short name → model ID).")
            if not sets:
                raise ValueError("Matrix routing needs at least one set (name → expression).")
            matrix["sets"] = sets
            settings = router.get("settings")
            if not isinstance(settings, (CommentedMap, dict)):
                settings = CommentedMap()
                router["settings"] = settings
            settings["matrix"] = matrix
            # The inactive engine's settings (if any) are preserved untouched.
            router["use"] = "matrix"
        else:
            router.pop("use", None)
            router.pop("settings", None)
            if not router:
                routing.pop("router", None)

        if not routing:
            data.pop("routing", None)


class PeersEditor(_MultiItemMixin, _SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("peers", parent)
        self._list = self.list = QListWidget(self)
        self.proxy = QLineEdit(self)
        self.proxy.setPlaceholderText("http://localhost:5900")
        self.api_key = QLineEdit(self)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.reveal_key = QCheckBox("Reveal", self)
        self.reveal_key.toggled.connect(self._reveal_toggled)
        key_row = QVBoxLayout()
        key_row.addWidget(self.api_key)
        key_row.addWidget(self.reveal_key)
        self.models = OrderedStringListEditor((), placeholder="Model ID exposed by this peer")
        self.strip_params = QLineEdit(self)
        self.strip_params.setPlaceholderText("Optional: parameter names to strip")
        self.set_params = StructuredYamlEditor("Optional: parameters to force (YAML mapping)", self)
        self.timeouts = {
            "connect": OptionalInt(30),
            "keepalive": OptionalInt(30),
            "responseHeader": OptionalInt(0),
            "tlsHandshake": OptionalInt(10),
            "idleConn": OptionalInt(90),
        }
        add = QPushButton("Add Peer")
        add.clicked.connect(self._add_peer)
        remove = QPushButton("Remove Peer")
        remove.clicked.connect(self._remove_peer)
        row = QVBoxLayout()
        row.addWidget(self.list, 1)
        form = QFormLayout()
        form.addRow("Proxy URL", self.proxy)
        form.addRow("API key", key_row)
        form.addRow("Models", self.models)
        form.addRow("Strip params", self.strip_params)
        form.addRow("Set params", self.set_params)
        for label, widget in self.timeouts.items():
            form.addRow(f"Timeout: {label}", widget)
        form.addRow(add)
        form.addRow(remove)
        row.addLayout(form)
        self.body.addLayout(row)
        self._multi_init()

    def _reveal_toggled(self, checked: bool) -> None:
        self.api_key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _add_peer(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Peer", "Peer ID:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._multi_ids():
            QMessageBox.warning(self, "Peers", "A peer with that ID already exists.")
            return
        self.list.addItem(QListWidgetItem(name))
        self.list.setCurrentItem(self.list.item(self.list.count() - 1))

    def _remove_peer(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._multi_drop(item.text())
            self.list.takeItem(self.list.row(item))

    def _load(self, data) -> None:
        self._multi_reset()
        self.list.clear()
        for peer_id in (data.get("peers") or {}):
            self.list.addItem(QListWidgetItem(str(peer_id)))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._multi_clear_widgets()

    def _apply(self, data) -> None:
        self._multi_stash()
        self._multi_apply_map(data, "peers")

    def _draft_from_disk(self, entry: dict) -> dict:
        filters = entry.get("filters") or {}
        timeouts = entry.get("timeouts") or {}
        return {
            "proxy": str(entry.get("proxy", "")),
            "api_key": str(entry.get("apiKey", "")),
            "models": [str(model) for model in (entry.get("models") or [])],
            "strip_params": str(filters.get("stripParams", "")),
            "set_params": structured_yaml_text(filters.get("setParams")),
            "timeouts": {key: (str(value) if value is not None else None) for key, value in timeouts.items()},
        }

    def _draft_to_widgets(self, draft: dict) -> None:
        self.proxy.setText(draft["proxy"])
        self.api_key.setText(draft["api_key"])
        self.models.set_values(draft["models"])
        self.strip_params.setText(draft["strip_params"])
        self.set_params.set_text(draft["set_params"])
        for key, widget in self.timeouts.items():
            raw = draft["timeouts"].get(key)
            widget.load(raw is not None, raw or 0)

    def _draft_from_widgets(self) -> dict:
        return {
            "proxy": self.proxy.text(),
            "api_key": self.api_key.text(),
            "models": self.models.values(),
            "strip_params": self.strip_params.text(),
            "set_params": self.set_params.raw(),
            "timeouts": {key: widget.explicit_raw() for key, widget in self.timeouts.items()},
        }

    def _multi_clear_widgets(self) -> None:
        self.proxy.clear()
        self.api_key.clear()
        self.models.set_values([])
        self.strip_params.clear()
        self.set_params.set_text("")
        for widget in self.timeouts.values():
            widget.reset()

    def _multi_validate_draft(self, peer_id: str, draft: dict) -> None:
        draft["proxy"] = draft["proxy"].strip()
        if not draft["proxy"]:
            raise ValueError(f"Peer {peer_id!r} needs a proxy URL.")
        draft["api_key"] = draft["api_key"].strip()
        draft["strip_params"] = draft["strip_params"].strip()
        draft["models"] = [str(model).strip() for model in draft["models"] if str(model).strip()]
        if not draft["models"]:
            raise ValueError(f"Peer {peer_id!r} needs at least one model.")
        if draft["set_params"]:
            parse_structured_yaml(draft["set_params"])
        for key, raw in draft["timeouts"].items():
            if raw is None:
                continue
            try:
                value = int(raw)
            except ValueError as error:
                raise ValueError(f"Peer {peer_id!r} timeout {key!r} must be a whole number.") from error
            draft["timeouts"][key] = str(value)

    def _multi_write_entry(self, entry, draft: dict) -> None:
        if str(entry.get("proxy", "")) != draft["proxy"]:
            entry["proxy"] = draft["proxy"]
        if draft["api_key"]:
            if str(entry.get("apiKey", "")) != draft["api_key"]:
                entry["apiKey"] = draft["api_key"]
        elif "apiKey" in entry:
            entry.pop("apiKey")
        if not self._same_sequence(entry.get("models") or [], draft["models"]):
            entry["models"] = list(draft["models"])
        filters = entry.get("filters")
        if not isinstance(filters, (CommentedMap, dict)):
            filters = None
        set_params = parse_structured_yaml(draft["set_params"])
        desired_filters: dict = {}
        if draft["strip_params"]:
            desired_filters["stripParams"] = draft["strip_params"]
        if set_params:
            desired_filters["setParams"] = set_params
        current_filters = {str(key): value for key, value in (filters.items() if filters is not None else [])}
        if current_filters != desired_filters:
            if filters is None:
                filters = CommentedMap()
                entry["filters"] = filters
            for key in list(filters):
                if str(key) not in desired_filters:
                    filters.pop(key)
            for key, value in desired_filters.items():
                filters[key] = value
            if not filters:
                entry.pop("filters")
        desired_timeouts = {key: int(raw) for key, raw in draft["timeouts"].items() if raw is not None}
        timeouts = entry.get("timeouts")
        if not isinstance(timeouts, (CommentedMap, dict)):
            timeouts = None
        current_timeouts = {str(key): value for key, value in (timeouts.items() if timeouts is not None else [])}
        if current_timeouts != desired_timeouts:
            if timeouts is None:
                timeouts = CommentedMap()
                entry["timeouts"] = timeouts
            for key in list(timeouts):
                if str(key) not in desired_timeouts:
                    timeouts.pop(key)
            for key, value in desired_timeouts.items():
                timeouts[key] = value
            if not timeouts:
                entry.pop("timeouts")
