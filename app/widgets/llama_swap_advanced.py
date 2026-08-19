"""Presence-aware visual editors for the advanced llama-swap configuration sections.

Each editor loads from the full round-trip mapping, applies only its own subtree, and
never writes a default that the user did not explicitly choose. Unknown fields at every
level survive; a failed validation leaves the file untouched.
"""
from __future__ import annotations

import re
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
from app.widgets.structured_yaml import StructuredYamlEditor

_MACRO_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_RESERVED_MACRO_NAMES = {"PID", "PORT", "MODEL_ID"}
_PROFILE_ID = re.compile(r"^[A-Z0-9_-]+$")


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


class GeneralSettingsEditor(_SectionEditor):
    MANAGED = ("healthCheckTimeout", "globalTTL", "unloadTimeout", "startPort", "sendLoadingState", "includeAliasesInList")

    def __init__(self, parent=None) -> None:
        super().__init__(None, parent)
        self.remove_button.setText("Reset to Defaults (remove stored keys)")
        self.health_check = OptionalInt(120)
        self.global_ttl = OptionalInt(0)
        self.unload = OptionalInt(10)
        self.start_port = OptionalInt(5800)
        self.send_loading = OptionalBool(False)
        self.include_aliases = OptionalBool(False)
        form = QFormLayout()
        form.addRow("Health check timeout (seconds)", self.health_check)
        form.addRow("Global TTL (seconds)", self.global_ttl)
        form.addRow("Unload timeout (seconds)", self.unload)
        form.addRow("Start port", self.start_port)
        form.addRow("Send loading state", self.send_loading)
        form.addRow("Include aliases in list", self.include_aliases)
        self.body.addLayout(form)
        self._widgets = {
            "healthCheckTimeout": self.health_check, "globalTTL": self.global_ttl, "unloadTimeout": self.unload,
            "startPort": self.start_port, "sendLoadingState": self.send_loading, "includeAliasesInList": self.include_aliases,
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


class ProfilesEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("profiles", parent)
        self.list = QListWidget(self)
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
        self.list.currentRowChanged.connect(self._selection_changed)

    def _add_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Profile", "Profile ID (uppercase letters, digits, _ or -):")
        if not ok or not name.strip():
            return
        if not _PROFILE_ID.match(name.strip()):
            QMessageBox.warning(self, "Profiles", "Profile IDs use uppercase letters, digits, underscore, or dash.")
            return
        self.list.addItem(QListWidgetItem(name.strip()))
        self.list.setCurrentItem(self.list.item(self.list.count() - 1))

    def _remove_profile(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.list.takeItem(self.list.row(item))

    def _selection_changed(self, row: int) -> None:
        if row < 0 or self._service is None:
            return
        data = self._service.load()
        entry = (data.get("profiles") or {}).get(self.list.item(row).text()) or {}
        self.description.setText(str(entry.get("description", "")))
        pins = entry.get("pins") or {}
        self.pins.set_items([(source, "" if target is None else str(target)) for source, target in pins.items()])

    def _load(self, data) -> None:
        self.list.clear()
        for profile_id in (data.get("profiles") or {}):
            self.list.addItem(QListWidgetItem(str(profile_id)))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.description.clear()
            self.pins.set_items([])

    def _apply(self, data) -> None:
        from ruamel.yaml.comments import CommentedMap

        if not self.list.count():
            data.pop("profiles", None)
            return
        profiles = data.get("profiles")
        if not isinstance(profiles, (CommentedMap, dict)):
            profiles = CommentedMap()
            data["profiles"] = profiles
        for index in range(self.list.count()):
            profile_id = self.list.item(index).text()
            entry = profiles.get(profile_id)
            if not isinstance(entry, (CommentedMap, dict)):
                entry = CommentedMap()
                profiles[profile_id] = entry
            description = self.description.text().strip() if index == self.list.currentRow() else str(entry.get("description", "")).strip()
            if description:
                entry["description"] = description
            else:
                entry.pop("description", None)
            pins = entry.get("pins")
            if not isinstance(pins, (CommentedMap, dict)):
                pins = CommentedMap()
                entry["pins"] = pins
            for key in list(pins):
                pins.pop(key)
            if index == self.list.currentRow():
                rows = self.pins.items()
                if not rows:
                    raise ValueError(f"Profile {profile_id!r} needs at least one pin.")
                for source, target in rows:
                    if not source.strip():
                        raise ValueError("Every pin needs a source model ID.")
                    pins[source.strip()] = target.strip() or None
            if not pins:
                raise ValueError(f"Profile {profile_id!r} needs at least one pin.")


class SelectorsEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("selectors", parent)
        self.list = QListWidget(self)
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
        self.list.currentRowChanged.connect(self._selection_changed)

    def _add_selector(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Selector", "Selector ID:")
        if not ok or not name.strip():
            return
        self.list.addItem(QListWidgetItem(name.strip()))
        self.list.setCurrentItem(self.list.item(self.list.count() - 1))

    def _remove_selector(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.list.takeItem(self.list.row(item))

    def _selection_changed(self, row: int) -> None:
        if row < 0 or self._service is None:
            return
        data = self._service.load()
        entry = (data.get("selectors") or {}).get(self.list.item(row).text()) or {}
        self._fill_detail(entry)

    def _fill_detail(self, entry) -> None:
        self.strategy.setCurrentText(str(entry.get("strategy", "warm")))
        self.spillover.edit.setEnabled(self.strategy.currentText() == "spillover")
        self.targets.set_values(entry.get("targets") or [])
        self.name.setText(str(entry.get("name", "")))
        self.description.setText(str(entry.get("description", "")))
        self.unlisted.setChecked(bool(entry.get("unlisted", False)))
        settings = entry.get("settings") or {}
        self.spillover.load("spillover" in settings, settings.get("spillover", 1))
        self.metadata.set_object(entry.get("metadata"))

    def _load(self, data) -> None:
        self.list.clear()
        for selector_id in (data.get("selectors") or {}):
            self.list.addItem(QListWidgetItem(str(selector_id)))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.strategy.setCurrentText("warm")
            self.targets.set_values([])
            self.name.clear()
            self.description.clear()
            self.unlisted.setChecked(False)
            self.spillover.load(False, 1)
            self.metadata.set_object(None)


    def _apply(self, data) -> None:
        from ruamel.yaml.comments import CommentedMap

        if not self.list.count():
            data.pop("selectors", None)
            return
        selectors = data.get("selectors")
        if not isinstance(selectors, (CommentedMap, dict)):
            selectors = CommentedMap()
            data["selectors"] = selectors
        for index in range(self.list.count()):
            selector_id = self.list.item(index).text()
            entry = selectors.get(selector_id)
            if not isinstance(entry, (CommentedMap, dict)):
                entry = CommentedMap()
                selectors[selector_id] = entry
            is_current = index == self.list.currentRow()
            strategy = self.strategy.currentText() if is_current else str(entry.get("strategy", "warm"))
            entry["strategy"] = strategy
            targets = self.targets.values() if is_current else list(entry.get("targets") or [])
            if not targets:
                raise ValueError(f"Selector {selector_id!r} needs at least one target.")
            entry["targets"] = list(targets)
            name = self.name.text().strip() if is_current else str(entry.get("name", "")).strip()
            if name:
                entry["name"] = name
            else:
                entry.pop("name", None)
            description = self.description.text().strip() if is_current else str(entry.get("description", "")).strip()
            if description:
                entry["description"] = description
            else:
                entry.pop("description", None)
            unlisted = self.unlisted.isChecked() if is_current else bool(entry.get("unlisted", False))
            if unlisted:
                entry["unlisted"] = True
            else:
                entry.pop("unlisted", None)
            settings = entry.get("settings")
            if not isinstance(settings, (CommentedMap, dict)):
                settings = CommentedMap()
                entry["settings"] = settings
            if strategy == "spillover":
                spillover = self.spillover.explicit() if is_current else settings.get("spillover")
                try:
                    value = int(spillover)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"Selector {selector_id!r} (spillover) needs an integer spillover count.") from error
                if value < 1:
                    raise ValueError(f"Selector {selector_id!r} (spillover) needs a spillover count of at least 1.")
                settings["spillover"] = value
            else:
                settings.pop("spillover", None)
            metadata = self.metadata.object() if is_current else entry.get("metadata")
            if metadata:
                entry["metadata"] = metadata
            else:
                entry.pop("metadata", None)
            if not settings:
                entry.pop("settings", None)


class RoutingEditor(_SectionEditor):
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
        self.groups_list = QListWidget(self.groups_box)
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
        self.matrix_vars = KeyValueTableEditor(("Var (a–h)", "Model ID"), self.matrix_box)
        self.matrix_costs = KeyValueTableEditor(("Var (a–h)", "Eviction cost (integer)"), self.matrix_box)
        self.matrix_sets = StructuredYamlEditor("Sets: list of {name, expr} or map name → expression", self.matrix_box)
        matrix_layout.addWidget(self.matrix_vars)
        matrix_layout.addWidget(self.matrix_costs)
        matrix_layout.addWidget(self.matrix_sets)
        router_form.addRow(self.matrix_box)
        self.body.addWidget(router_group)

        self.groups_list.currentRowChanged.connect(self._group_selection_changed)
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
        self.groups_list.addItem(QListWidgetItem(name.strip()))
        self.groups_list.setCurrentItem(self.groups_list.item(self.groups_list.count() - 1))

    def _remove_group(self) -> None:
        item = self.groups_list.currentItem()
        if item is not None:
            self.groups_list.takeItem(self.groups_list.row(item))

    def _group_selection_changed(self, row: int) -> None:
        if row < 0 or self._service is None:
            return
        data = self._service.load()
        groups = (((data.get("routing") or {}).get("router") or {}).get("settings") or {}).get("groups") or {}
        group = groups.get(self.groups_list.item(row).text()) or {}
        self.group_swap.load("swap" in group, group.get("swap", True))
        self.group_exclusive.load("exclusive" in group, group.get("exclusive", True))
        self.group_persistent.load("persistent" in group, group.get("persistent", False))
        self.group_members.set_values(group.get("members") or [])
        self.group_members.set_choices(self._model_ids(data))

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
        self.groups_list.clear()
        groups = (router.get("settings") or {}).get("groups") or {}
        for group_name in groups:
            self.groups_list.addItem(QListWidgetItem(str(group_name)))
        if self.groups_list.count():
            self.groups_list.setCurrentRow(0)
        else:
            self.group_swap.reset()
            self.group_exclusive.reset()
            self.group_persistent.reset()
            self.group_members.set_values([])
        settings = router.get("settings") or {}
        matrix = settings.get("matrix") or {}
        self.matrix_vars.set_items([(str(var), str(model)) for var, model in (matrix.get("vars") or {}).items()])
        self.matrix_costs.set_items([(str(var), str(cost)) for var, cost in (matrix.get("evict_costs") or {}).items()])
        self.matrix_sets.set_object(matrix.get("sets"))
        self.group_members.set_choices(self._model_ids(data))

    def _group_detail(self, group_name: str) -> dict:
        group: dict = {}
        for key, widget in (("swap", self.group_swap), ("exclusive", self.group_exclusive), ("persistent", self.group_persistent)):
            value = widget.explicit()
            if value is not None:
                group[key] = value
        members = self.group_members.values()
        if members:
            group["members"] = members
        return group

    def _apply(self, data) -> None:
        from ruamel.yaml.comments import CommentedMap

        routing: CommentedMap = CommentedMap()
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
        use = self.router_use.currentData()
        if use == "group":
            groups: CommentedMap = CommentedMap()
            existing_groups = (((data.get("routing") or {}).get("router") or {}).get("settings") or {}).get("groups") or {}
            for index in range(self.groups_list.count()):
                group_name = self.groups_list.item(index).text()
                if index == self.groups_list.currentRow():
                    groups[group_name] = CommentedMap(self._group_detail(group_name))
                else:
                    existing = existing_groups.get(group_name)
                    groups[group_name] = CommentedMap(existing) if isinstance(existing, dict) else CommentedMap()
            if not groups:
                raise ValueError("Group routing needs at least one group.")
            settings = CommentedMap()
            settings["groups"] = groups
            router = CommentedMap()
            router["use"] = "group"
            router["settings"] = settings
            routing["router"] = router
        elif use == "matrix":
            matrix = CommentedMap()
            vars_rows = self.matrix_vars.items()
            if vars_rows:
                matrix_vars = CommentedMap()
                for var, model in vars_rows:
                    if not var.strip():
                        raise ValueError("Matrix vars need a variable name (a–h).")
                    matrix_vars[var.strip()] = model.strip()
                matrix["vars"] = matrix_vars
            cost_rows = self.matrix_costs.items()
            if cost_rows:
                costs = CommentedMap()
                for var, text in cost_rows:
                    if not var.strip():
                        raise ValueError("Matrix eviction costs need a variable name (a–h).")
                    try:
                        costs[var.strip()] = int(text)
                    except ValueError as error:
                        raise ValueError(f"Eviction cost for {var!r} must be an integer.") from error
                matrix["evict_costs"] = costs
            sets = self.matrix_sets.object()
            if sets:
                matrix["sets"] = sets
            if not matrix:
                raise ValueError("Matrix routing needs vars, evict_costs, or sets.")
            settings = CommentedMap()
            settings["matrix"] = matrix
            router = CommentedMap()
            router["use"] = "matrix"
            router["settings"] = settings
            routing["router"] = router
        if routing:
            data["routing"] = routing
        else:
            data.pop("routing", None)


class PeersEditor(_SectionEditor):
    def __init__(self, parent=None) -> None:
        super().__init__("peers", parent)
        self.list = QListWidget(self)
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
        self.list.currentRowChanged.connect(self._selection_changed)

    def _reveal_toggled(self, checked: bool) -> None:
        self.api_key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _add_peer(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Peer", "Peer ID:")
        if not ok or not name.strip():
            return
        self.list.addItem(QListWidgetItem(name.strip()))
        self.list.setCurrentItem(self.list.item(self.list.count() - 1))

    def _remove_peer(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.list.takeItem(self.list.row(item))

    def _selection_changed(self, row: int) -> None:
        if row < 0 or self._service is None:
            return
        data = self._service.load()
        peer = (data.get("peers") or {}).get(self.list.item(row).text()) or {}
        self._fill_detail(peer)

    def _fill_detail(self, peer) -> None:
        self.proxy.setText(str(peer.get("proxy", "")))
        self.api_key.setText(str(peer.get("apiKey", "")))
        self.models.set_values(peer.get("models") or [])
        filters = peer.get("filters") or {}
        self.strip_params.setText(str(filters.get("stripParams", "")))
        self.set_params.set_object(filters.get("setParams"))
        timeouts = peer.get("timeouts") or {}
        for key, widget in self.timeouts.items():
            widget.load(key in timeouts, timeouts.get(key))

    def _load(self, data) -> None:
        self.list.clear()
        for peer_id in (data.get("peers") or {}):
            self.list.addItem(QListWidgetItem(str(peer_id)))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.proxy.clear()
            self.api_key.clear()
            self.models.set_values([])
            self.strip_params.clear()
            self.set_params.set_object(None)
            for widget in self.timeouts.values():
                widget.load(False, None)

    def _apply(self, data) -> None:
        from ruamel.yaml.comments import CommentedMap

        if not self.list.count():
            data.pop("peers", None)
            return
        peers = data.get("peers")
        if not isinstance(peers, (CommentedMap, dict)):
            peers = CommentedMap()
            data["peers"] = peers
        for index in range(self.list.count()):
            peer_id = self.list.item(index).text()
            is_current = index == self.list.currentRow()
            entry = peers.get(peer_id)
            if not isinstance(entry, (CommentedMap, dict)):
                entry = CommentedMap()
                peers[peer_id] = entry
            proxy = self.proxy.text().strip() if is_current else str(entry.get("proxy", "")).strip()
            if not proxy:
                raise ValueError(f"Peer {peer_id!r} needs a proxy URL.")
            entry["proxy"] = proxy
            models = self.models.values() if is_current else list(entry.get("models") or [])
            if not models:
                raise ValueError(f"Peer {peer_id!r} needs at least one model.")
            entry["models"] = list(models)
            api_key = self.api_key.text().strip() if is_current else str(entry.get("apiKey", "")).strip()
            if api_key:
                entry["apiKey"] = api_key
            else:
                entry.pop("apiKey", None)
            filters = entry.get("filters")
            if not isinstance(filters, (CommentedMap, dict)):
                filters = CommentedMap()
                entry["filters"] = filters
            strip_params = self.strip_params.text().strip() if is_current else str(filters.get("stripParams", "")).strip()
            if strip_params:
                filters["stripParams"] = strip_params
            else:
                filters.pop("stripParams", None)
            set_params = self.set_params.object() if is_current else filters.get("setParams")
            if set_params:
                filters["setParams"] = set_params
            else:
                filters.pop("setParams", None)
            if not filters:
                entry.pop("filters", None)
            timeouts = entry.get("timeouts")
            if not isinstance(timeouts, (CommentedMap, dict)):
                timeouts = CommentedMap()
                entry["timeouts"] = timeouts
            for key, widget in self.timeouts.items():
                value = widget.explicit() if is_current else timeouts.get(key)
                if value is None:
                    timeouts.pop(key, None)
                else:
                    timeouts[key] = value
            if not timeouts:
                entry.pop("timeouts", None)
