from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)
from app.models.command import Command
from app.models.hf_download import HfTarget, TARGET_LABELS
from app.models.command import CommandArgument
from app.services.command_parser import parse_command
from app.services.command_runner import CommandRunner
from app.services.flag_catalog import FlagCatalog
from app.services.llama_swap_service import DuplicateModelError, LlamaSwapError, LlamaSwapService, suggested_model_id
from app.services.validation import validate_command
from app.services.llama_cpp_installation import LlamaCppInstallationService
from app.server import SERVER_COMMAND, server_executable_path
from app.services.hf_cli_service import HfCliService
from app.services.memory_test_service import MemoryTestService
from app.services.benchmark_service import BenchmarkService
from app.services.server_verification_service import ServerVerificationService
from app.settings import AppSettings
from app.widgets.command_builder import CommandBuilder
from app.widgets.config_viewer import ConfigViewer
from app.widgets.hf_download_tab import HfDownloadTab
from app.widgets.output_console import OutputConsole
from app.widgets.memory_options import MemoryTestOptionsDialog
from app.widgets.memory_results import MemoryResultsDialog
from app.widgets.benchmark_options import BenchmarkOptionsDialog
from app.widgets.benchmark_results import BenchmarkResultsDialog
from app.widgets.guided_presets import ContextKvPresetDialog, CustomMtpPresetDialog, DeviceSplitPresetDialog
from app.widgets.model_dialog import ModelDialog

# Download destinations that map to a folder-backed builder row refreshed in place.
_HF_REFRESH_FLAG = {
    HfTarget.MODELS.value: "--model",
    HfTarget.MMProj.value: "--mmproj",
    HfTarget.DRAFTERS.value: "--spec-draft-model",
    HfTarget.TEMPLATES.value: "--chat-template-file",
}


class SettingsPage(QWidget):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.fields: dict[str, QLineEdit] = {}
        layout = QVBoxLayout(self)
        self._scanned_folder = ""
        layout.addWidget(QLabel("<h2>Settings</h2>"))
        layout.addWidget(QLabel("Paths are retained when unavailable so the configuration can be repaired without re-entering them."))
        form = QFormLayout()
        entries = [
            ("llama_cpp_folder", "llama.cpp Folder", True),
            ("models_folder", "Models Folder", True),
            ("mmproj_folder", "MMProj Folder", True),
            ("drafters_folder", "Drafters Folder", True),
            ("template_folder", "Chat Template Folder", True),
            ("llama_swap_config", "llama-swap config file", False),
        ]
        for key, label, directory in entries:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            field = QLineEdit(getattr(settings, key))
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _, k=key, d=directory: self._browse(k, d))
            field.textChanged.connect(lambda _value, k=key: self._mark_unavailable(k))
            row_layout.addWidget(field, 1)
            row_layout.addWidget(browse)
            form.addRow(label, row)
            self.fields[key] = field
        layout.addLayout(form)

        detected = QGroupBox("Detected Tools")
        detected_form = QFormLayout(detected)
        self.server_path = QLabel(SERVER_COMMAND)
        self.server_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.fit_combo = QComboBox()
        self.bench_combo = QComboBox()
        self.rescan = QPushButton("Rescan")
        self.rescan.clicked.connect(self.rescan_tools)
        detected_form.addRow("llama-server", self.server_path)
        detected_form.addRow("llama-fit-params", self.fit_combo)
        detected_form.addRow("llama-bench", self.bench_combo)
        detected_form.addRow(self.rescan)
        layout.addWidget(detected)

        self.backup_limit = QLineEdit(str(settings.backup_limit))
        form.addRow("Backups retained", self.backup_limit)
        self.ready_timeout = QSpinBox()
        self.ready_timeout.setRange(10, 1800)
        self.ready_timeout.setSuffix(" s")
        self.ready_timeout.setValue(int(getattr(settings, "server_ready_timeout", 180)))
        form.addRow("Server readiness timeout", self.ready_timeout)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch()
        for key in self.fields:
            self._mark_unavailable(key)
        self.rescan_tools()

    def _browse(self, key: str, directory: bool) -> None:
        current = self.fields[key].text()
        selected = QFileDialog.getExistingDirectory(self, "Choose folder", current) if directory else QFileDialog.getOpenFileName(self, "Choose file", current, "All files (*)")[0]
        if selected:
            self.fields[key].setText(selected)
            if key == "llama_cpp_folder":
                self.rescan_tools()

    def _mark_unavailable(self, key: str) -> None:
        value = self.fields[key].text()
        self.fields[key].setStyleSheet("color: #b91c1c;" if value and not Path(value).exists() else "")

    def rescan_tools(self) -> None:
        self._scanned_folder = self.fields["llama_cpp_folder"].text().strip()
        installation = LlamaCppInstallationService.discover(self.fields["llama_cpp_folder"].text().strip())
        self.server_path.setText(SERVER_COMMAND if server_executable_path().is_file() else f"⚠ Missing: {SERVER_COMMAND}")
        self._populate_tool_combo(self.fit_combo, installation.fit_params.paths, self.settings.llama_fit_params_executable)
        self._populate_tool_combo(self.bench_combo, installation.bench.paths, self.settings.llama_bench_executable)

    @staticmethod
    def _populate_tool_combo(combo: QComboBox, paths, selected: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        if not paths:
            combo.addItem("⚠ Not found", "")
        else:
            for path in paths:
                combo.addItem(str(path), str(path))
            index = combo.findData(selected)
            combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def save(self) -> None:
        if self.fields["llama_cpp_folder"].text().strip() != self._scanned_folder:
            self.rescan_tools()
        for key, field in self.fields.items():
            setattr(self.settings, key, field.text().strip())
        self.settings.llama_fit_params_executable = self.fit_combo.currentData() or ""
        self.settings.llama_bench_executable = self.bench_combo.currentData() or ""
        try:
            self.settings.backup_limit = max(1, int(self.backup_limit.text()))
        except ValueError:
            self.settings.backup_limit = 10
            self.backup_limit.setText("10")
        self.settings.server_ready_timeout = self.ready_timeout.value()
        self.settings.save()
        self.window().statusBar().showMessage("Settings saved.", 5_000)
        if isinstance(self.window(), MainWindow):
            self.window().settings_saved()


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, catalog: FlagCatalog) -> None:
        super().__init__()
        self.settings, self.catalog = settings, catalog
        self.setWindowTitle("Llama Test Tool")
        self.resize(1200, 850)
        if settings.window_geometry:
            self.restoreGeometry(bytes.fromhex(settings.window_geometry))
        self._create_menu()
        self.tabs = QTabWidget()
        self.builder = CommandBuilder(settings, catalog)
        self.console = OutputConsole()
        builder_page = QWidget()
        builder_layout = QVBoxLayout(builder_page)
        builder_layout.setContentsMargins(0, 0, 0, 0)
        builder_splitter = QSplitter(Qt.Orientation.Vertical, builder_page)
        builder_splitter.addWidget(self.builder)
        builder_splitter.addWidget(self.console)
        builder_splitter.setStretchFactor(0, 3)
        builder_splitter.setStretchFactor(1, 2)
        builder_layout.addWidget(builder_splitter, 1)
        self.hf_service = HfCliService(self)
        self.hf_tab = HfDownloadTab(settings, self.hf_service)
        self.viewer = ConfigViewer(settings)
        self.settings_page = SettingsPage(settings)
        self.tabs.addTab(self.hf_tab, "Hugging Face")
        self.tabs.addTab(builder_page, "Command Builder")
        self.tabs.addTab(self.viewer, "llama-swap Config")
        self.tabs.addTab(self.settings_page, "Settings")
        self.setCentralWidget(self.tabs)
        self.runner = CommandRunner(self)
        self.verification_service = ServerVerificationService(self.runner, self)
        self.benchmark_service = BenchmarkService(self)
        self.memory_service = MemoryTestService(self)
        self.builder.changed.connect(self.persist_builder)
        self.builder.test_requested.connect(self.test_server)
        self.builder.stop_requested.connect(self.verification_service.cancel)
        self.builder.memory_test_requested.connect(self.memory_test)
        self.builder.memory_options_requested.connect(self.memory_options)
        self.builder.memory_cancel_requested.connect(self.memory_service.cancel)
        self.builder.benchmark_requested.connect(self.benchmark)
        self.builder.benchmark_options_requested.connect(self.benchmark_options)
        self.builder.benchmark_cancel_requested.connect(self.benchmark_service.cancel)
        self.builder.add_to_swap_requested.connect(self.add_to_swap)
        self.hf_tab.folders_changed.connect(self._on_hf_folders_changed)
        self.hf_tab.use_requested.connect(self._on_hf_use_requested)
        self.viewer.load_requested.connect(self.load_from_swap)
        self.viewer.status.connect(lambda message: self.statusBar().showMessage(message, 5_000))
        self.runner.output.connect(self.console.append)
        self.runner.state_changed.connect(self._process_state)
        self.verification_service.stage_changed.connect(self.builder.verify_status.handle_stage)
        self.verification_service.completed.connect(self._verification_complete)
        self.memory_service.state_changed.connect(self._memory_state)
        self.memory_service.completed.connect(self._memory_complete)
        self.benchmark_service.state_changed.connect(self._benchmark_state)
        self.benchmark_service.completed.connect(self._benchmark_complete)
        self.statusBar().showMessage("Ready")

    def _create_menu(self) -> None:
        catalog_menu = self.menuBar().addMenu("Catalog")
        refresh = QAction("Refresh llama.cpp Arguments", self)
        refresh.triggered.connect(self.refresh_catalog)
        catalog_menu.addAction(refresh)
        presets = self.menuBar().addMenu("Presets")
        for label, key in (("Add MMProj", "mmproj"), ("Built-in MTP", "mtp"), ("N-gram Mod", "ngram"), ("Custom Template", "template")):
            action = QAction(label, self)
            action.triggered.connect(lambda _, value=key: self.builder.apply_preset(value))
            presets.addAction(action)
        for label, handler in (("Context + KV Cache", self.context_kv_preset), ("Device Split", self.device_split_preset), ("Custom MTP / External Drafter", self.custom_mtp_preset)):
            action = QAction(label, self)
            action.triggered.connect(handler)
            presets.addAction(action)

    def settings_saved(self) -> None:
        self.builder.rebuild()
        self.viewer.refresh()

    def _on_hf_folders_changed(self, targets: list) -> None:
        names = []
        for target in targets:
            flag = _HF_REFRESH_FLAG.get(target)
            if flag and self.builder.refresh_folder_for(flag):
                names.append(TARGET_LABELS.get(target, str(target)))
        if names:
            self.statusBar().showMessage(f"Download finished — refreshed {', '.join(names)} selectors.", 8_000)
        else:
            self.statusBar().showMessage("Download finished.", 8_000)

    def _on_hf_use_requested(self, flag: str, path: str) -> None:
        try:
            self.builder.set_argument(flag, [path], source_type="manual")
            self.statusBar().showMessage(f"Set {flag} to {path}", 8_000)
        except Exception as error:  # noqa: BLE001 - surface any catalog issue in the status bar
            self.statusBar().showMessage(f"Could not set {flag}: {error}", 8_000)

    def _remove_arguments(self, canonical_name: str) -> None:
        before = len(self.builder.command.arguments)
        self.builder.command.arguments = [
            argument for argument in self.builder.command.arguments
            if (spec := self.catalog.find(argument.flag)) is None or spec.canonical_name != canonical_name
        ]
        if len(self.builder.command.arguments) != before:
            self.builder.rebuild()

    def _apply_preset_values(self, values: dict[str, list[str]], owned: tuple[str, ...] = ()) -> None:
        for name, argument_values in values.items():
            self.builder.set_argument(name, argument_values)
        # Owned flags are fully managed by the preset dialog: an owned flag absent from
        # ``values`` is removed so clearing the dialog cannot leave stale flags behind.
        # The --cpu-moe/--n-cpu-moe pair is one logical setting with two spellings.
        for name in owned:
            if name not in values:
                self._remove_arguments(name)

    def context_kv_preset(self) -> None:
        dialog = ContextKvPresetDialog(self.catalog, self.builder.command, self)
        if dialog.exec():
            try:
                self._apply_preset_values(dialog.values())
            except ValueError as error:
                QMessageBox.warning(self, "Context + KV Cache", str(error))

    def device_split_preset(self) -> None:
        dialog = DeviceSplitPresetDialog(self.catalog, self.builder.command, self)
        if dialog.exec():
            try:
                self._apply_preset_values(dialog.values(), owned=("--cpu-moe", "--n-cpu-moe"))
            except ValueError as error:
                QMessageBox.warning(self, "Device Split", str(error))

    def custom_mtp_preset(self) -> None:
        dialog = CustomMtpPresetDialog(self.settings, self.catalog, self.builder.command, self)
        if dialog.exec():
            try:
                self._apply_preset_values(dialog.values())
            except ValueError as error:
                QMessageBox.warning(self, "Custom MTP / External Drafter", str(error))

    def persist_builder(self) -> None:
        self.settings.last_command = self.builder.command.to_dict()
        self.settings.save()

    def refresh_catalog(self) -> None:
        try:
            catalog = FlagCatalog.refresh()
            catalog.save(Path(__file__).parent.parent / "data" / "llama_server_flags.json")
        except Exception as error:
            QMessageBox.warning(self, "Refresh llama.cpp arguments", f"Refresh failed; continuing with the bundled catalog.\n\n{error}")
            return
        self.catalog = catalog
        self.builder.set_catalog(catalog)
        self.statusBar().showMessage(f"Updated argument catalog with {len(catalog.specs)} flags.", 8_000)

    def test_server(self) -> None:
        issues = validate_command(self.builder.command, self.catalog)
        errors = [issue.message for issue in issues if issue.severity == "error"]
        if errors:
            QMessageBox.warning(self, "Cannot test server", "\n".join(errors))
            return
        try:
            self.verification_service.verify(
                self.builder.command,
                self.catalog,
                executable=LlamaCppInstallationService.active_server(self.settings),
                timeout_seconds=int(getattr(self.settings, "server_ready_timeout", 180)),
            )
        except RuntimeError as error:
            QMessageBox.warning(self, "Test Server", str(error))

    def _verification_complete(self, result) -> None:
        process_running = self.runner.running
        self.builder.verify_status.show_result(result, process_running)
        if result.failed_stage:
            message = f"Verification failed at {result.failed_stage.title()}."
            if process_running:
                message += " Server process still running — use Stop."
            self.statusBar().showMessage(message, 12_000)
        elif result.verified:
            self.statusBar().showMessage("Server verified — process still running; Stop to end it.", 12_000)
        else:
            self.statusBar().showMessage("Verification incomplete — see panel.", 8_000)

    def _process_state(self, state: str) -> None:
        self.builder.set_running(state in {"Running", "Stopping"})
        self.statusBar().showMessage(f"Process: {state}")

    def memory_options(self) -> None:
        dialog = MemoryTestOptionsDialog(self.settings, self)
        if dialog.exec():
            dialog.save_to(self.settings)
            self.statusBar().showMessage("Memory test options saved.", 5_000)

    def memory_test(self) -> None:
        issues = validate_command(self.builder.command, self.catalog)
        errors = [issue.message for issue in issues if issue.severity == "error"]
        if errors:
            QMessageBox.warning(self, "Cannot run memory test", "\n".join(errors))
            return
        try:
            self.memory_service.start(
                self.builder.command,
                LlamaCppInstallationService.active_fit_params(self.settings),
                self.catalog,
                fit_target=self.settings.memory_fit_target,
                fit_context=self.settings.memory_fit_context,
            )
        except RuntimeError as error:
            QMessageBox.warning(self, "Memory Test", str(error))

    def _memory_state(self, state: str) -> None:
        self.builder.set_memory_running(state not in {"Memory test complete", "Memory test failed"})
        self.statusBar().showMessage(state)

    def _memory_complete(self, result) -> None:
        self.builder.set_memory_running(False)
        dialog = MemoryResultsDialog(result, self)
        dialog.apply_requested.connect(self.apply_fitted_arguments)
        dialog.exec()

    def benchmark_options(self) -> None:
        dialog = BenchmarkOptionsDialog(self.settings, self)
        if dialog.exec():
            try:
                dialog.save_to(self.settings)
            except ValueError as error:
                QMessageBox.warning(self, "Benchmark Options", str(error))
            else:
                self.statusBar().showMessage("Benchmark options saved.", 5_000)

    def benchmark(self) -> None:
        issues = validate_command(self.builder.command, self.catalog)
        errors = [issue.message for issue in issues if issue.severity == "error"]
        if errors:
            QMessageBox.warning(self, "Benchmark", "\n".join(errors))
            return
        dialog = BenchmarkOptionsDialog(self.settings, self)
        if not dialog.exec():
            return
        try:
            options = dialog.save_to(self.settings)
            self.benchmark_service.start(self.builder.command, LlamaCppInstallationService.active_bench(self.settings), self.catalog, options)
        except (RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Benchmark", str(error))

    def _benchmark_state(self, state: str) -> None:
        self.builder.set_benchmark_running(state not in {"Benchmark complete", "Benchmark failed"})
        self.statusBar().showMessage(state)

    def _benchmark_complete(self, result) -> None:
        self.builder.set_benchmark_running(False)
        BenchmarkResultsDialog(result, self).exec()

    def apply_fitted_arguments(self, tokens: tuple[str, ...]) -> None:
        updates: list[tuple[object, list[str]]] = []
        index = 0
        while index < len(tokens):
            flag = tokens[index]
            spec = self.catalog.find(flag)
            if spec is None or not flag.startswith("-"):
                QMessageBox.warning(self, "Apply Fitted Parameters", "The fitted output includes an unknown argument and was not applied.")
                return
            count = spec.parameter_count
            if index + count >= len(tokens):
                QMessageBox.warning(self, "Apply Fitted Parameters", f"{flag} has incomplete fitted values and was not applied.")
                return
            updates.append((spec, list(tokens[index + 1:index + 1 + count])))
            index += count + 1
        for spec, values in updates:
            existing = next((argument for argument in self.builder.command.arguments if self.catalog.find(argument.flag) and self.catalog.find(argument.flag).canonical_name == spec.canonical_name), None)
            if existing:
                existing.values = values
            else:
                self.builder.command.arguments.append(CommandArgument(spec.preferred_name, values, "fitted"))
        self.builder.rebuild()
        self.statusBar().showMessage("Applied fitted parameters to the command builder.", 5_000)

    def swap_command(self) -> str:
        command = self.builder.command.copy()
        port = next((argument for argument in command.arguments if self.catalog.find(argument.flag) and self.catalog.find(argument.flag).canonical_name == "--port"), None)
        if port is None:
            command.arguments.insert(1, CommandArgument("--port", ["${PORT}"], "llama_swap"))
        return command.rendered_lines()

    def add_to_swap(self) -> None:
        if not self.settings.llama_swap_config:
            self.tabs.setCurrentWidget(self.settings_page)
            QMessageBox.information(self, "Add to llama-swap", "Select a llama-swap configuration file in Settings first.")
            return
        issues = validate_command(self.builder.command, self.catalog)
        errors = [issue.message for issue in issues if issue.severity == "error"]
        if errors:
            QMessageBox.warning(self, "Cannot add to llama-swap", "\n".join(errors))
            return
        model_path = self.builder.command.model_path()
        context = next((argument.values[0] for argument in self.builder.command.arguments if (spec := self.catalog.find(argument.flag)) and spec.canonical_name == "--ctx-size" and argument.values), "")
        has_mmproj = any((spec := self.catalog.find(argument.flag)) and spec.canonical_name == "--mmproj" and argument.values and argument.values[0] for argument in self.builder.command.arguments)
        dialog = ModelDialog(suggested_model_id(str(model_path or "llama-model")), context, has_mmproj, self)
        while dialog.exec():
            model_id = dialog.model_id.text().strip()
            if not model_id:
                QMessageBox.warning(dialog, "Model ID", "A model ID is required.")
                continue
            service = LlamaSwapService(self.settings.llama_swap_config, self.settings.backup_limit)
            try:
                service.add_model(model_id, self.swap_command(), dialog.display_name.text().strip(), dialog.metadata())
            except DuplicateModelError:
                choice = QMessageBox(dialog)
                choice.setWindowTitle("Model already exists")
                choice.setText(f"{model_id!r} already exists.")
                choice.setInformativeText("Replace only its cmd field, choose a different model ID, or cancel.")
                replace = choice.addButton("Replace command", QMessageBox.ButtonRole.AcceptRole)
                another = choice.addButton("Choose another ID", QMessageBox.ButtonRole.ActionRole)
                choice.addButton(QMessageBox.StandardButton.Cancel)
                choice.exec()
                if choice.clickedButton() is replace:
                    try:
                        service.replace_command(model_id, self.swap_command())
                    except LlamaSwapError as error:
                        QMessageBox.critical(dialog, "Replace llama-swap command", str(error))
                        return
                    break
                if choice.clickedButton() is another:
                    dialog.model_id.setFocus()
                    dialog.model_id.selectAll()
                    continue
                return
            except LlamaSwapError as error:
                QMessageBox.critical(dialog, "Add llama-swap model", str(error))
                return
            break
        else:
            return
        self.viewer.refresh()
        self.tabs.setCurrentWidget(self.viewer)
        self.statusBar().showMessage("llama-swap model saved; backup created.", 8_000)

    def load_from_swap(self, raw_command: str) -> None:
        result = parse_command(raw_command, self.catalog)
        if result.command is None:
            QMessageBox.information(self, "Raw Command Mode", f"This command is preserved in the Config tab's editable Raw Command Mode.\n\n{result.raw_reason}")
            return
        previous_executable = result.command.executable
        result.command.executable = SERVER_COMMAND
        self.builder.command = result.command
        self.builder.rebuild()
        self.tabs.setCurrentWidget(self.tabs.widget(0))
        warning = " This entry used another server executable; the Command Builder uses the configured build-mixed llama-server." if previous_executable != SERVER_COMMAND else ""
        self.statusBar().showMessage(f"Loaded llama-swap command into the visual builder.{warning}", 8_000)

    def closeEvent(self, event) -> None:
        self.settings.window_geometry = bytes(self.saveGeometry()).hex()
        self.persist_builder()
        self.verification_service.cancel()
        if self.memory_service.running:
            self.memory_service.cancel()
        if self.benchmark_service.running:
            self.benchmark_service.cancel()
        self.hf_service.shutdown()
        super().closeEvent(event)
