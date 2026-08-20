from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from app.services.llama_swap_service import LlamaSwapError, LlamaSwapService, suggested_model_id
from app.widgets.ordered_list_editor import OrderedStringListEditor
from app.widgets.optional_setting import OptionalBool, OptionalInt, OptionalSettingWidget
from app.widgets.structured_yaml import StructuredYamlEditor, parse_structured_yaml
from app.widgets.llama_swap_advanced import (
    ActivityPerformanceEditor,
    GeneralSettingsEditor,
    HooksEditor,
    LoggingSettingsEditor,
    MacrosEditor,
    PeersEditor,
    ProfilesEditor,
    RoutingEditor,
    SecurityEditor,
    SelectorsEditor,
    UpstreamEditor,
)


def _model_field_text(value) -> str:
    """Render a model string field for the text editor; legacy non-string values are shown, not coerced silently."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class ConfigViewer(QWidget):
    load_requested = Signal(str)
    status = Signal(str)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.current_id = None
        self._global_present: set[str] = set()
        self._global_effective: dict[str, object] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        # ---------------------------------------------------------------- Models
        models_page = QWidget()
        page = QVBoxLayout(models_page)
        page.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal, models_page)
        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(left)
        self.search.setPlaceholderText("Search models…")
        self.search.textChanged.connect(self._filter)
        self.list = QListWidget(left)
        self.list.itemClicked.connect(self._select)
        buttons = QHBoxLayout()
        self.load_button = QPushButton("Load Into Builder", left)
        self.load_button.clicked.connect(self._load)
        self.save_command_button = QPushButton("Save Command", left)
        self.save_command_button.clicked.connect(self._save)
        self.duplicate_button = QPushButton("Duplicate", left)
        self.duplicate_button.clicked.connect(self._duplicate)
        self.remove_button = QPushButton("Remove", left)
        self.remove_button.clicked.connect(self._remove)
        for button in (self.load_button, self.save_command_button, self.duplicate_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch()
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.list, 1)
        left_layout.addLayout(buttons)
        splitter.addWidget(left)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        raw_group = QGroupBox("llama-swap Command", right)
        raw_layout = QVBoxLayout(raw_group)
        self.raw = QTextEdit(raw_group)
        raw_layout.addWidget(self.raw, 1)
        command_actions = QHBoxLayout()
        save = QPushButton("Save Command", raw_group)
        save.clicked.connect(self._save)
        reset = QPushButton("Reset To Global Timeouts", raw_group)
        reset.clicked.connect(self._inherit_timeouts)
        command_actions.addWidget(save)
        command_actions.addWidget(reset)
        command_actions.addStretch()
        raw_layout.addLayout(command_actions)
        right_layout.addWidget(raw_group, 1)

        settings_group = QGroupBox("Model Settings", right)
        settings_form = QFormLayout(settings_group)
        self.name = QLineEdit(settings_group)
        self.name.setPlaceholderText("Optional display name (blank = model ID)")
        self.description = QLineEdit(settings_group)
        self.description.setPlaceholderText("Optional description")
        self.aliases = OrderedStringListEditor(use_combo=True, parent=settings_group)
        settings_form.addRow("Display name", self.name)
        settings_form.addRow("Description", self.description)
        settings_form.addRow("Aliases", self.aliases)
        self.use_model_name = QLineEdit(settings_group)
        self.use_model_name.setPlaceholderText("Optional: model name sent upstream (blank = omit)")
        self.check_endpoint = QLineEdit(settings_group)
        self.check_endpoint.setPlaceholderText("Optional endpoint path, e.g. /health (blank = omit)")
        self.unlisted = QCheckBox("Unlisted (hidden from default model list)", settings_group)
        settings_form.addRow(self.use_model_name)
        settings_form.addRow(self.check_endpoint)
        settings_form.addRow(self.unlisted)
        # Presence-aware: unchecked = key absent (inherit global), checked with
        # a value = explicitly stored. ttl accepts -1 ("never unload"); the
        # save path writes the exact explicit value and removes the key when
        # the field is left unset.
        self.ttl = OptionalInt(0, settings_group)
        self.ttl.edit.setPlaceholderText("unset = inherit global; -1 = never unload")
        self.unload = OptionalInt(0, settings_group)
        self.unload.edit.setPlaceholderText("unset = inherit global")
        settings_form.addRow("TTL (seconds)", self.ttl)
        settings_form.addRow("Unload timeout (seconds)", self.unload)
        self.concurrency_limit = OptionalInt(0, settings_group)
        self.send_loading_state = OptionalBool(False, settings_group)
        settings_form.addRow("Concurrency limit", self.concurrency_limit)
        settings_form.addRow("Send loading state", self.send_loading_state)
        advanced = QGroupBox("Advanced (current upstream ModelConfig)", settings_group)
        advanced_form = QFormLayout(advanced)
        self.cmd_stop = OptionalSettingWidget("", placeholder="Optional: command that stops the model process (blank = omit)", parent=advanced)
        self.proxy = OptionalSettingWidget("", placeholder="Optional: proxy URL, e.g. http://localhost:5900 (blank = omit)", parent=advanced)
        advanced_form.addRow("Stop command", self.cmd_stop)
        advanced_form.addRow("Proxy", self.proxy)
        self.env = OrderedStringListEditor(placeholder="NAME=value (one per entry)", parent=advanced)
        advanced_form.addRow("Environment", self.env)
        self.metadata_editor = StructuredYamlEditor("Optional metadata (YAML mapping; extra /v1/models fields)", advanced)
        advanced_form.addRow("Metadata", self.metadata_editor)
        self.strip_params = OptionalSettingWidget("", placeholder="Optional: comma-separated request params to strip (blank = omit)", parent=advanced)
        self.set_params_editor = StructuredYamlEditor("Optional setParams (YAML mapping of param → value, any type)", advanced)
        self.set_params_by_id_editor = StructuredYamlEditor("Optional setParamsByID (YAML mapping of alias → param mapping)", advanced)
        advanced_form.addRow("Filters: stripParams", self.strip_params)
        advanced_form.addRow("Filters: setParams", self.set_params_editor)
        advanced_form.addRow("Filters: setParamsByID", self.set_params_by_id_editor)
        self.model_timeouts = {
            "connect": OptionalInt(30, advanced),
            "keepalive": OptionalInt(30, advanced),
            "responseHeader": OptionalInt(0, advanced),
            "tlsHandshake": OptionalInt(10, advanced),
            "expectContinue": OptionalInt(1, advanced),
            "idleConn": OptionalInt(90, advanced),
        }
        for label, widget in self.model_timeouts.items():
            advanced_form.addRow(f"Timeout: {label}", widget)
        self.ignore_websockets = OptionalBool(False, advanced)
        advanced_form.addRow("Compat: ignoreWebsockets", self.ignore_websockets)
        settings_form.addRow(advanced)
        self.save_settings_button = QPushButton("Save Model Settings", settings_group)
        self.save_settings_button.clicked.connect(self._save_model_settings)
        settings_actions_widget = QWidget(settings_group)
        settings_actions = QHBoxLayout(settings_actions_widget)
        settings_actions.setContentsMargins(0, 0, 0, 0)
        settings_actions.addWidget(self.save_settings_button)
        settings_actions.addStretch()
        settings_form.addRow(settings_actions_widget)
        caps_group = QGroupBox("Capabilities", settings_group)
        caps_layout = QVBoxLayout(caps_group)
        self.caps_configured = QCheckBox("Configure capabilities (unchecked = runtime defaults)", caps_group)
        self.caps_in = {capability: QCheckBox(capability, caps_group) for capability in ("text", "audio", "image")}
        self.caps_out = {capability: QCheckBox(capability, caps_group) for capability in ("text", "audio", "image")}
        self.caps_tools = QCheckBox("tools", caps_group)
        self.caps_reranker = QCheckBox("reranker", caps_group)
        self.caps_context = OptionalInt(0, caps_group)
        caps_grid = QVBoxLayout()
        caps_row1 = QHBoxLayout()
        caps_row1.addWidget(QLabel("In:"))
        for widget in self.caps_in.values():
            caps_row1.addWidget(widget)
        caps_row1.addStretch()
        caps_row2 = QHBoxLayout()
        caps_row2.addWidget(QLabel("Out:"))
        for widget in self.caps_out.values():
            caps_row2.addWidget(widget)
        caps_row2.addStretch()
        caps_row3 = QHBoxLayout()
        caps_row3.addWidget(self.caps_tools)
        caps_row3.addWidget(self.caps_reranker)
        caps_row3.addWidget(QLabel("Context:"))
        caps_row3.addWidget(self.caps_context)
        caps_row3.addStretch()
        caps_grid.addLayout(caps_row1)
        caps_grid.addLayout(caps_row2)
        caps_grid.addLayout(caps_row3)
        caps_layout.addWidget(self.caps_configured)
        caps_layout.addLayout(caps_grid)
        settings_form.addRow(caps_group)
        right_layout.addWidget(settings_group, 1)
        splitter.addWidget(right)
        page.addWidget(splitter, 1)
        self.tabs.addTab(models_page, "Models")

        # -------------------------------------------------- Advanced sections
        self.general_editor = GeneralSettingsEditor()
        self.logging_editor = LoggingSettingsEditor()
        self.activity_editor = ActivityPerformanceEditor()
        self.security_editor = SecurityEditor()
        self.macros_editor = MacrosEditor()
        self.hooks_editor = HooksEditor()
        self.upstream_editor = UpstreamEditor()
        self.profiles_editor = ProfilesEditor()
        self.selectors_editor = SelectorsEditor()
        self.routing_editor = RoutingEditor()
        self.peers_editor = PeersEditor()
        for editor, title in (
            (self.general_editor, "General"),
            (self.logging_editor, "Logging"),
            (self.activity_editor, "Activity / Performance"),
            (self.security_editor, "Security"),
            (self.macros_editor, "Macros"),
            (self.hooks_editor, "Hooks"),
            (self.upstream_editor, "Upstream"),
            (self.profiles_editor, "Profiles"),
            (self.selectors_editor, "Selectors"),
            (self.routing_editor, "Routing"),
            (self.peers_editor, "Peers"),
        ):
            editor.saved.connect(self._editor_saved)
            self.tabs.addTab(editor, title)

        self._set_actions_enabled(False)
        self.refresh()

    def _editor_saved(self, message: str) -> None:
        self.status.emit(message)
        self.refresh()

    def service(self) -> LlamaSwapService | None:
        return LlamaSwapService(self.settings.llama_swap_config, self.settings.backup_limit) if self.settings.llama_swap_config else None

    def refresh(self) -> None:
        selected = self.current_id
        self.list.clear()
        service = self.service()
        if service is None:
            self.status.emit("Choose a llama-swap config.yaml in Settings.")
            return
        try:
            data = service.load()
        except LlamaSwapError as error:
            self.status.emit(str(error))
            return
        models = data.get("models") or {}
        for model_id, entry in models.items():
            item = QListWidgetItem(str(entry.get("name") or model_id))
            item.setData(Qt.ItemDataRole.UserRole, str(model_id))
            self.list.addItem(item)
        self._filter(self.search.text())
        current = None
        if selected is not None:
            for index in range(self.list.count()):
                item = self.list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == selected:
                    current = item
                    break
        self._select(current, restore=selected)
        for editor in (
            self.general_editor, self.logging_editor, self.activity_editor, self.security_editor, self.macros_editor,
            self.hooks_editor, self.upstream_editor, self.profiles_editor, self.selectors_editor, self.routing_editor, self.peers_editor,
        ):
            editor.load(data, service)
        self.status.emit("llama-swap configuration refreshed.")

    def _select(self, item: QListWidgetItem | None, restore: str | None = None) -> None:
        service = self.service()
        if item is None or service is None:
            self.current_id = None
            self._set_actions_enabled(False)
            return
        self.current_id = item.data(Qt.ItemDataRole.UserRole)
        try:
            entry = service.load()["models"][self.current_id]
        except LlamaSwapError:
            self.current_id = None
            self._set_actions_enabled(False)
            return
        self._set_actions_enabled(True)
        self.raw.setPlainText(str(entry.get("cmd", "")))
        self.name.setText(str(entry.get("name", "")))
        self.description.setText(str(entry.get("description", "")))
        self.aliases.set_values(entry.get("aliases") or [])
        self.use_model_name.setText(_model_field_text(entry.get("useModelName")))
        self.check_endpoint.setText(_model_field_text(entry.get("checkEndpoint")))
        self.unlisted.setChecked(bool(entry.get("unlisted", False)))
        self.ttl.load("ttl" in entry, entry.get("ttl", 0))
        self.unload.load("unloadTimeout" in entry, entry.get("unloadTimeout", 0))
        self.concurrency_limit.load("concurrencyLimit" in entry, entry.get("concurrencyLimit", 0))
        self.send_loading_state.load("sendLoadingState" in entry, entry.get("sendLoadingState", False))
        self.cmd_stop.load("cmdStop" in entry, entry.get("cmdStop") if isinstance(entry.get("cmdStop"), str) else "")
        self.proxy.load("proxy" in entry, entry.get("proxy") if isinstance(entry.get("proxy"), str) else "")
        self.env.set_values([str(value) for value in (entry.get("env") or [])])
        self.metadata_editor.set_object(entry.get("metadata"))
        filters = entry.get("filters") or {}
        self.strip_params.load("stripParams" in filters, filters.get("stripParams") if isinstance(filters.get("stripParams"), str) else "")
        self.set_params_editor.set_object(filters.get("setParams"))
        self.set_params_by_id_editor.set_object(filters.get("setParamsByID"))
        timeouts = entry.get("timeouts") or {}
        for label, widget in self.model_timeouts.items():
            widget.load(label in timeouts, timeouts.get(label, 0))
        compat = entry.get("compat") or {}
        self.ignore_websockets.load("ignoreWebsockets" in compat, bool(compat.get("ignoreWebsockets", False)))
        capabilities = entry.get("capabilities") or {}
        self.caps_configured.setChecked(bool(capabilities))
        for capability, checkbox in self.caps_in.items():
            checkbox.setChecked(capability in capabilities.get("in", []))
        for capability, checkbox in self.caps_out.items():
            checkbox.setChecked(capability in capabilities.get("out", []))
        self.caps_tools.setChecked(bool(capabilities.get("tools", False)))
        self.caps_reranker.setChecked(bool(capabilities.get("reranker", False)))
        self.caps_context.load("context" in capabilities, capabilities.get("context", 0))
        if restore is not None:
            for index in range(self.list.count()):
                if self.list.item(index).data(Qt.ItemDataRole.UserRole) == restore:
                    self.list.setCurrentRow(index)
                    break

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.load_button.setEnabled(enabled)
        self.save_command_button.setEnabled(enabled)
        self.duplicate_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.ttl.setEnabled(enabled)
        self.unload.setEnabled(enabled)
        self.name.setEnabled(enabled)
        self.description.setEnabled(enabled)
        self.use_model_name.setEnabled(enabled)
        self.check_endpoint.setEnabled(enabled)

    def _filter(self, text: str) -> None:
        text = text.lower()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(text not in (item.text() + " " + str(item.data(Qt.ItemDataRole.UserRole))).lower())

    @staticmethod
    def _model_path(command: str) -> str | None:
        import re
        match = re.search(r"(?:-m|--model)\s+(?:\"([^\"]+)\"|(\S+))", command)
        return next((group for group in match.groups() if group), None) if match else None

    def _load(self) -> None:
        if self.current_id:
            self.load_requested.emit(self.raw.toPlainText())

    def _save(self) -> None:
        if not self.current_id:
            return
        service = self.service()
        if service is None:
            return
        command = self.raw.toPlainText().strip()
        if not command:
            QMessageBox.warning(self, "Save Command", "The command is empty.")
            return
        try:
            service.replace_command(self.current_id, command)
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Save Command", str(error))
            return
        self.status.emit("Saved command; backup created.")
        self.refresh()

    def _duplicate(self) -> None:
        if not self.current_id:
            return
        service = self.service()
        if service is None:
            return
        target, ok = QInputDialog.getText(self, "Duplicate Model", "New model ID:", text=suggested_model_id(self.current_id))
        if not ok or not target:
            return
        try:
            service.duplicate(self.current_id, target)
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Duplicate Model", str(error))
            return
        self.status.emit(f"Created model {target}.")
        self.refresh()

    def _remove(self) -> None:
        if not self.current_id:
            return
        service = self.service()
        if service is None:
            return
        if QMessageBox.question(self, "Remove Model", f"Remove {self.current_id} from llama-swap?") != QMessageBox.StandardButton.Yes:
            return
        try:
            service.remove_model(self.current_id)
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Remove Model", str(error))
            return
        self.status.emit(f"Removed model {self.current_id}.")
        self.refresh()
    def _inherit_timeouts(self) -> None:
        if QMessageBox.question(self, "Inherit global timeouts", "Remove per-model TTL and unloadTimeout from every model (inherit global defaults)?") != QMessageBox.StandardButton.Yes:
            return
        service = self.service()
        if service is None:
            return
        try:
            count = service.inherit_model_timeouts()
        except LlamaSwapError as error:
            QMessageBox.critical(self, "Inherit Timeouts", str(error))
            return
        self.status.emit(f"Updated {count} model(s); backup created.")
        self.refresh()

    def _save_model_settings(self) -> None:
        if not self.current_id:
            return
        service = self.service()
        if service is None:
            return
        try:
            values: dict[str, object] = {}
            name = self.name.text().strip()
            values["name"] = name or None
            description = self.description.text().strip()
            values["description"] = description or None
            values["aliases"] = self.aliases.values() or None
            values["useModelName"] = self.use_model_name.text().strip() or None
            values["checkEndpoint"] = self.check_endpoint.text().strip() or None
            values["unlisted"] = True if self.unlisted.isChecked() else None
            concurrency = self.concurrency_limit.explicit()
            values["concurrencyLimit"] = concurrency
            loading = self.send_loading_state.explicit()
            values["sendLoadingState"] = loading
            # None removes the key (unset/inherit global); an explicit int -1/0/N
            # is stored verbatim — never collapsed to absence.
            values["ttl"] = self.ttl.explicit()
            values["unloadTimeout"] = self.unload.explicit()
            values["cmdStop"] = self.cmd_stop.explicit()
            values["proxy"] = self.proxy.explicit()
            values["env"] = [str(v) for v in self.env.values() if str(v).strip()] or None
            values["metadata"] = parse_structured_yaml(self.metadata_editor.raw())
            filters = {}
            strip = self.strip_params.explicit()
            if strip:
                filters["stripParams"] = strip
            set_params = parse_structured_yaml(self.set_params_editor.raw())
            if set_params is not None:
                filters["setParams"] = set_params
            set_params_by_id = parse_structured_yaml(self.set_params_by_id_editor.raw())
            if set_params_by_id is not None:
                filters["setParamsByID"] = set_params_by_id
            values["filters"] = filters or None
            timeouts = {label: widget.explicit() for label, widget in self.model_timeouts.items()}
            timeouts = {label: value for label, value in timeouts.items() if value is not None}
            values["timeouts"] = timeouts or None
            websockets = self.ignore_websockets.explicit()
            values["compat"] = {"ignoreWebsockets": websockets} if websockets is not None else None
            capabilities: dict[str, object] = {}
            if self.caps_configured.isChecked():
                inputs = [capability for capability, checkbox in self.caps_in.items() if checkbox.isChecked()]
                outputs = [capability for capability, checkbox in self.caps_out.items() if checkbox.isChecked()]
                if inputs:
                    capabilities["in"] = inputs
                if outputs:
                    capabilities["out"] = outputs
                if self.caps_tools.isChecked():
                    capabilities["tools"] = True
                if self.caps_reranker.isChecked():
                    capabilities["reranker"] = True
                context = self.caps_context.explicit()
                if context is not None:
                    capabilities["context"] = context
            values["capabilities"] = capabilities or None
            service.update_model_metadata(self.current_id, values)
        except (ValueError, LlamaSwapError) as error:
            QMessageBox.critical(self, "Save Model Settings", str(error))
            return
        self.status.emit("Saved model settings; backup created.")
        self.refresh()
