from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from app.services.llama_swap_service import LlamaSwapError, LlamaSwapService, suggested_model_id
from app.widgets.ordered_list_editor import OrderedStringListEditor
from app.widgets.optional_setting import OptionalBool, OptionalInt
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
        self.ttl_mode = QComboBox(settings_group)
        self.ttl_mode.addItem("Global (inherit)", "global")
        self.ttl_mode.addItem("Inherit timeout", "inherit")
        self.ttl_mode.addItem("Custom…", "custom")
        self.ttl = QLineEdit(settings_group)
        self.ttl_mode.currentIndexChanged.connect(lambda: self.ttl.setEnabled(self.ttl_mode.currentData() == "custom"))
        self.unload_mode = QComboBox(settings_group)
        self.unload_mode.addItem("Global (inherit)", "global")
        self.unload_mode.addItem("Inherit timeout", "inherit")
        self.unload_mode.addItem("Custom…", "custom")
        self.unload = QLineEdit(settings_group)
        self.unload_mode.currentIndexChanged.connect(lambda: self.unload.setEnabled(self.unload_mode.currentData() == "custom"))
        settings_form.addRow("TTL (seconds)", self.ttl_mode)
        settings_form.addRow("Custom TTL (−1 = never)", self.ttl)
        settings_form.addRow("Unload timeout (seconds)", self.unload_mode)
        settings_form.addRow("Custom unload timeout", self.unload)
        self.concurrency_limit = OptionalInt(0, settings_group)
        self.send_loading_state = OptionalBool(False, settings_group)
        settings_form.addRow("Concurrency limit", self.concurrency_limit)
        settings_form.addRow("Send loading state", self.send_loading_state)

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
        self._select(self.list.currentItem() if self.list.count() else None, restore=selected)
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
        ttl = entry.get("ttl")
        if ttl == -1:
            self.ttl_mode.setCurrentIndex(1)
            self.ttl.setText("-1")
        elif isinstance(ttl, int) and ttl != -1:
            self.ttl_mode.setCurrentIndex(2)
            self.ttl.setText(str(ttl))
        else:
            self.ttl_mode.setCurrentIndex(0)
            self.ttl.setText("")
        unload = entry.get("unloadTimeout")
        if isinstance(unload, int):
            self.unload_mode.setCurrentIndex(2)
            self.unload.setText(str(unload))
        else:
            self.unload_mode.setCurrentIndex(0)
            self.unload.setText("")
        self.concurrency_limit.load("concurrencyLimit" in entry, entry.get("concurrencyLimit", 0))
        self.send_loading_state.load("sendLoadingState" in entry, entry.get("sendLoadingState", False))
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
        self.ttl_mode.setEnabled(enabled)
        self.unload_mode.setEnabled(enabled)
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
        if QMessageBox.question(self, "Inherit global timeouts", "Set every model TTL to -1 and unloadTimeout to 0?") != QMessageBox.StandardButton.Yes:
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
            ttl_mode = self.ttl_mode.currentData()
            if ttl_mode == "custom":
                values["ttl"] = int(self.ttl.text())
            elif ttl_mode == "inherit":
                values["ttl"] = None
            unload_mode = self.unload_mode.currentData()
            if unload_mode == "custom":
                values["unloadTimeout"] = int(self.unload.text())
            elif unload_mode == "inherit":
                values["unloadTimeout"] = None
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
