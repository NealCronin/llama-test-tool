"""Hugging Face download tab: official ``hf`` CLI, queue, dry-run preview.

The form renders a command preview and enqueues immutable request snapshots.
There is deliberately no token field: authentication uses the ``hf`` CLI's own
stored credentials (``hf auth login``) or an inherited ``HF_TOKEN`` variable.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import Signal
from app.models.hf_download import (
    HfAuthStatus,
    HfCliCapabilities,
    HfCliInfo,
    HfDownloadRequest,
    HfDownloadResult,
    HfDryRunReport,
    HfRepoType,
    HfSelectionMode,
    HfTarget,
    TARGET_LABELS,
    TARGET_SETTING_KEYS,
)
from app.services.hf_cli_service import HfCliError, build_download_argv, render_command_line, redact_secrets

_SEPARATED_LINE_EDIT = """
QPlainTextEdit {
    border: 1px solid #9a9a9a;
    border-radius: 4px;
    padding: 2px 6px;
    background: #ffffff;
    color: #1f1f1f;
}
"""

_CANDIDATE_SUFFIXES = {
    HfTarget.MODELS: (".gguf",),
    HfTarget.MMProj: (".gguf",),
    HfTarget.DRAFTERS: (".gguf",),
}
_USE_AS_FLAG = {
    HfTarget.MODELS: "--model",
    HfTarget.MMProj: "--mmproj",
    HfTarget.DRAFTERS: "--spec-draft-model",
}


class HfDownloadTab(QWidget):
    """Download UI. All process work happens in HfCliService (QProcess)."""

    folders_changed = Signal(list)  # configured HfTarget values whose selectors need refreshing
    use_requested = Signal(str, str)  # canonical flag, file path

    def __init__(self, settings, service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.service = service
        self._info = HfCliInfo(path="")
        self._capabilities: HfCliCapabilities | None = None
        self._auth = HfAuthStatus()
        self._previewing = False
        self._last_result: HfDownloadResult | None = None
        self._build_ui()
        self._restore_settings()
        service.info_ready.connect(self._on_info)
        service.capabilities_ready.connect(self._on_capabilities)
        service.auth_ready.connect(self._on_auth)
        service.job_output.connect(self._on_job_output)
        service.job_state_changed.connect(lambda *_args: self._refresh_table())
        service.job_finished.connect(self._on_job_finished)
        service.queue_changed.connect(self._refresh_table)
        service.preview_finished.connect(self._on_preview_finished)
        self._update_preview()
        service.probe()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Detecting hf CLI…", self)
        self.status_label.setStyleSheet("color: #222222; font-weight: bold;")
        refresh = QPushButton("Refresh Status", self)
        refresh.clicked.connect(lambda: self.service.probe())
        login = QPushButton("Open Login Terminal", self)
        login.clicked.connect(self._open_login_terminal)
        copy_login = QPushButton("Copy Login Command", self)
        copy_login.clicked.connect(self._copy_login_command)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(refresh)
        status_row.addWidget(login)
        status_row.addWidget(copy_login)
        root.addLayout(status_row)

        request_group = QGroupBox("Download Request", self)
        request_layout = QVBoxLayout(request_group)
        repo_row = QHBoxLayout()
        self.repo_edit = QLineEdit(self)
        self.repo_edit.setPlaceholderText("owner/repo (e.g. Qwen/Qwen3-4B-GGUF)")
        self.repo_type_combo = QComboBox(self)
        for value in HfRepoType:
            self.repo_type_combo.addItem(value.value.capitalize(), value.value)
        revision_row = QHBoxLayout()
        self.revision_edit = QLineEdit(self)
        self.revision_edit.setPlaceholderText("revision: branch, tag, or commit (optional)")
        selection_row = QHBoxLayout()
        self.entire_radio = QRadioButton("Entire Repository", self)
        self.exact_radio = QRadioButton("Exact File Names", self)
        self.patterns_radio = QRadioButton("Include / Exclude Patterns", self)
        self.entire_radio.setChecked(True)
        selection_row.addWidget(self.entire_radio)
        selection_row.addWidget(self.exact_radio)
        selection_row.addWidget(self.patterns_radio)
        selection_row.addStretch(1)
        self.files_edit = QLineEdit(self)
        self.files_edit.setPlaceholderText("exact file names, comma-separated (e.g. model-Q4_K_M.gguf)")
        self.include_edit = QLineEdit(self)
        self.include_edit.setPlaceholderText("include globs, comma-separated (e.g. *.gguf)")
        self.exclude_edit = QLineEdit(self)
        self.exclude_edit.setPlaceholderText("exclude globs, comma-separated (optional)")
        for edit in (self.files_edit, self.include_edit, self.exclude_edit):
            edit.setReadOnly(False)
        request_layout.addWidget(QLabel("Repo ID", self))
        repo_row.addWidget(QLabel("Repository Type", self))
        repo_row.addWidget(self.repo_type_combo, 1)
        request_layout.addLayout(repo_row)
        request_layout.addWidget(QLabel("Revision", self))
        request_layout.addLayout(revision_row)
        request_layout.addWidget(QLabel("Selection", self))
        request_layout.addLayout(selection_row)
        request_layout.addWidget(self.files_edit)
        request_layout.addWidget(self.include_edit)
        request_layout.addWidget(self.exclude_edit)
        root.addWidget(request_group)

        destination_group = QGroupBox("Destination", self)
        destination_layout = QVBoxLayout(destination_group)
        destination_row = QHBoxLayout()
        self.destination_combo = QComboBox(self)
        for target in HfTarget:
            self.destination_combo.addItem(TARGET_LABELS[target], target.value)
        self.custom_local_edit = QLineEdit(self)
        self.custom_local_edit.setPlaceholderText("custom local folder (only for Custom Folder)")
        browse_custom = QPushButton("Browse…", self)
        browse_custom.clicked.connect(lambda: self._browse(self.custom_local_edit))
        self.cache_edit = QLineEdit(self)
        self.cache_edit.setPlaceholderText("custom cache directory (only for HF Cache Only, optional)")
        browse_cache = QPushButton("Browse…", self)
        browse_cache.clicked.connect(lambda: self._browse(self.cache_edit))
        destination_row.addWidget(self.destination_combo, 1)
        destination_row.addWidget(self.custom_local_edit, 1)
        destination_row.addWidget(browse_custom)
        destination_row.addWidget(self.cache_edit, 1)
        destination_row.addWidget(browse_cache)
        self.destination_note = QLabel("", self)
        self.destination_note.setWordWrap(True)
        self.destination_note.setStyleSheet("color: #555555;")
        destination_layout.addLayout(destination_row)
        destination_layout.addWidget(self.destination_note)
        root.addWidget(destination_group)

        options_group = QGroupBox("Options", self)
        options_layout = QVBoxLayout(options_group)
        self.force_check = QCheckBox("Force Download (re-download even if cached)", self)
        workers_row = QHBoxLayout()
        self.workers_check = QCheckBox("Override max workers", self)
        self.workers_spin = QSpinBox(self)
        self.workers_spin.setRange(1, 128)
        workers_row.addWidget(self.workers_check)
        workers_row.addWidget(self.workers_spin)
        workers_row.addStretch(1)
        options_layout.addWidget(self.force_check)
        options_layout.addLayout(workers_row)
        root.addWidget(options_group)

        self.command_preview = QLabel("Command Preview", self)
        self.command_preview.setWordWrap(True)
        self.command_preview.setStyleSheet("font-family: Consolas, monospace; color: #1f3b73;")
        action_row = QHBoxLayout()
        self.preview_button = QPushButton("Preview Download (dry-run)", self)
        self.preview_button.clicked.connect(self._preview_download)
        self.start_button = QPushButton("Add to Queue / Start", self)
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._add_to_queue)
        action_row.addWidget(self.preview_button)
        action_row.addWidget(self.start_button)
        action_row.addStretch(1)
        root.addWidget(self.command_preview)
        root.addLayout(action_row)

        preview_box = QPlainTextEdit(self)
        preview_box.setReadOnly(True)
        preview_box.setPlaceholderText("Preview Download (dry-run) output appears here.")
        preview_box.setFixedHeight(120)
        preview_box.setStyleSheet(_SEPARATED_LINE_EDIT)
        self.preview_box = preview_box
        root.addWidget(preview_box)

        queue_group = QGroupBox("Download Queue (one download runs at a time)", self)
        queue_layout = QVBoxLayout(queue_group)
        self.queue_table = QTableWidget(0, 6, queue_group)
        self.queue_table.setHorizontalHeaderLabels(["Repository", "Type", "Selection", "Destination", "State", "Result"])
        self.queue_table.horizontalHeader().setStretchLastSection(True)
        self.queue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        queue_actions = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel Active", self)
        self.cancel_button.clicked.connect(self.service.cancel_active)
        self.remove_button = QPushButton("Remove Queued", self)
        self.remove_button.clicked.connect(self._remove_selected)
        self.retry_button = QPushButton("Retry Failed", self)
        self.retry_button.clicked.connect(self._retry_selected)
        self.up_button = QPushButton("Move Up", self)
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button = QPushButton("Move Down", self)
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        for button in (self.cancel_button, self.remove_button, self.retry_button, self.up_button, self.down_button):
            queue_actions.addWidget(button)
        queue_actions.addStretch(1)
        queue_layout.addWidget(self.queue_table)
        queue_layout.addLayout(queue_actions)
        root.addWidget(queue_group)

        console_box = QPlainTextEdit(self)
        console_box.setReadOnly(True)
        console_box.setPlaceholderText("Download output appears here.")
        console_box.setStyleSheet(_SEPARATED_LINE_EDIT)
        self.console_box = console_box
        root.addWidget(console_box, 1)

        post_row = QHBoxLayout()
        self.open_folder_button = QPushButton("Open Folder", self)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.copy_path_button = QPushButton("Copy Path", self)
        self.copy_path_button.clicked.connect(self._copy_path)
        self.use_as_buttons: dict[HfTarget, QPushButton] = {}
        for target, flag in _USE_AS_FLAG.items():
            button = QPushButton(f"Use as {TARGET_LABELS[target].split(' ')[0]}", self)
            button.clicked.connect(lambda _checked=False, flag=flag: self._use_selected_candidate(flag))
            self.use_as_buttons[target] = button
            post_row.addWidget(button)
        post_row.addStretch(1)
        self.post_status = QLabel("", self)
        self.post_status.setWordWrap(True)
        post_row.addWidget(self.post_status, 1)
        for button in (self.open_folder_button, self.copy_path_button):
            post_row.insertWidget(0, button)
        for target in HfTarget:
            if target in self.use_as_buttons:
                self.use_as_buttons[target].setEnabled(False)
        root.addLayout(post_row)

        self._selection = (self.entire_radio, self.exact_radio, self.patterns_radio)
        for radio in self._selection:
            radio.toggled.connect(self._update_selection_visibility)
        self.destination_combo.currentIndexChanged.connect(self._update_destination)
        self.repo_edit.textChanged.connect(self._update_preview)
        self.repo_type_combo.currentIndexChanged.connect(self._update_preview)
        self.revision_edit.textChanged.connect(self._update_preview)
        for radio in self._selection:
            radio.toggled.connect(self._update_preview)
        for edit in (self.files_edit, self.include_edit, self.exclude_edit, self.custom_local_edit, self.cache_edit):
            edit.textChanged.connect(self._update_preview)
        self.force_check.toggled.connect(self._on_option_changed)
        self.workers_check.toggled.connect(self._on_option_changed)
        self.workers_spin.valueChanged.connect(self._on_option_changed)
        self.destination_combo.currentIndexChanged.connect(self._on_option_changed)
        self.repo_type_combo.currentIndexChanged.connect(self._on_option_changed)
        self._update_selection_visibility()
        self._update_destination()

    def _browse(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Folder")
        if path:
            edit.setText(path)

    # ------------------------------------------------------------------ settings
    def _restore_settings(self) -> None:
        settings = self.settings
        self.destination_combo.setCurrentIndex(self.destination_combo.findData(settings.hf_destination or HfTarget.MODELS.value))
        self.repo_type_combo.setCurrentIndex(self.repo_type_combo.findData(settings.hf_repo_type or HfRepoType.MODEL.value))
        self.custom_local_edit.setText(settings.hf_custom_local_dir)
        self.cache_edit.setText(settings.hf_custom_cache_dir)
        self.force_check.setChecked(bool(settings.hf_force_download))
        self.workers_check.setChecked(bool(settings.hf_worker_override))
        self.workers_spin.setValue(int(settings.hf_max_workers or 8))

    def _persist(self) -> None:
        settings = self.settings
        settings.hf_destination = self.destination_combo.currentData() or HfTarget.MODELS.value
        settings.hf_repo_type = self.repo_type_combo.currentData() or HfRepoType.MODEL.value
        settings.hf_custom_local_dir = self.custom_local_edit.text().strip()
        settings.hf_custom_cache_dir = self.cache_edit.text().strip()
        settings.hf_force_download = self.force_check.isChecked()
        settings.hf_worker_override = self.workers_check.isChecked()
        settings.hf_max_workers = self.workers_spin.value()
        settings.save()

    def _on_option_changed(self, *_args) -> None:
        self._persist()
        self._update_preview()

    # ------------------------------------------------------------------ status
    def _on_info(self, info: HfCliInfo) -> None:
        self._info = info
        self._update_status()

    def _on_capabilities(self, caps: HfCliCapabilities) -> None:
        self._capabilities = caps
        supported = caps.dry_run
        self.preview_button.setEnabled(supported)
        if supported:
            self.preview_button.setToolTip("Runs `hf download … --dry-run` (does not touch the destination).")
        else:
            note = "The installed hf CLI has no --dry-run (needs huggingface_hub 1.0.0+). Preview Download is disabled; update with: python -m pip install -U huggingface_hub"
            self.preview_button.setToolTip(note)
            self.destination_note.setText(note)
        self._update_status()
        self._update_preview()

    def _on_auth(self, auth: HfAuthStatus) -> None:
        self._auth = auth
        self._update_status()

    def _update_status(self) -> None:
        if not self._info.path:
            self.status_label.setText("hf CLI not found on PATH. Install with: python -m pip install -U huggingface_hub")
            return
        version = f" (huggingface_hub {self._info.hub_version})" if self._info.hub_version else ""
        self.status_label.setText(f"hf CLI: {self._info.path}{version} — {self._auth.label}")

    def _open_login_terminal(self) -> None:
        path = self._info.path
        if not path:
            self.post_status.setText("hf CLI not found; install it first: python -m pip install -U huggingface_hub")
            return
        if os.name == "nt":
            subprocess.Popen([path, "auth", "login"], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            self.post_status.setText("Login terminal opened; complete the login there, then press Refresh Status.")
        else:
            for terminal in ("x-terminal-emulator", "konsole", "xterm"):
                if subprocess.run(["which", terminal], capture_output=True).returncode == 0:
                    subprocess.Popen([terminal, "-e", path, "auth", "login"])
                    self.post_status.setText("Login terminal opened; complete the login there, then press Refresh Status.")
                    return
            self.post_status.setText("No terminal emulator found; use Copy Login Command and run it in a terminal.")

    def _copy_login_command(self) -> None:
        from PySide6.QtWidgets import QApplication

        command = f"{shlex.quote(self._info.path)} auth login" if self._info.path else "hf auth login"
        QApplication.clipboard().setText(command)
        self.post_status.setText(f"Login command copied: {command}")

    # ------------------------------------------------------------------ form
    def _selected_mode(self) -> HfSelectionMode:
        if self.exact_radio.isChecked():
            return HfSelectionMode.EXACT
        if self.patterns_radio.isChecked():
            return HfSelectionMode.PATTERNS
        return HfSelectionMode.ENTIRE

    def _update_selection_visibility(self) -> None:
        mode = self._selected_mode()
        self.files_edit.setEnabled(mode is HfSelectionMode.EXACT)
        self.include_edit.setEnabled(mode is HfSelectionMode.PATTERNS)
        self.exclude_edit.setEnabled(mode is HfSelectionMode.PATTERNS)

    def _update_destination(self) -> None:
        target = HfTarget(self.destination_combo.currentData() or HfTarget.MODELS.value)
        self.custom_local_edit.setEnabled(target is HfTarget.CUSTOM)
        self.cache_edit.setEnabled(target is HfTarget.CACHE)
        if target in TARGET_SETTING_KEYS:
            folder = getattr(self.settings, TARGET_SETTING_KEYS[target])
            self.destination_note.setText(f"Downloads to the configured {TARGET_LABELS[target]}: {folder or '(not set — pick it in Application Paths)'}")
        elif target is HfTarget.CACHE:
            self.destination_note.setText("Downloads to the Hugging Face cache only (no --local-dir). Application selectors are not refreshed.")
        else:
            self.destination_note.setText("Downloads to the custom folder you choose. Application selectors are not refreshed.")

    def _split_list(self, text: str) -> list[str]:
        return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]

    def _build_request(self) -> tuple[HfDownloadRequest | None, str]:
        """Build a frozen request snapshot from the form; error message on failure."""
        target = HfTarget(self.destination_combo.currentData() or HfTarget.MODELS.value)
        if target in TARGET_SETTING_KEYS:
            local_dir = getattr(self.settings, TARGET_SETTING_KEYS[target], "")
            if not local_dir:
                return None, f"Configure the {TARGET_LABELS[target]} in Application Paths first."
            cache_dir = ""
        elif target is HfTarget.CACHE:
            local_dir, cache_dir = "", self.cache_edit.text().strip()
        else:
            local_dir, cache_dir = self.custom_local_edit.text().strip(), ""
        mode = self._selected_mode()
        try:
            return (
                HfDownloadRequest(
                    repo_id=self.repo_edit.text().strip(),
                    target=target,
                    repo_type=HfRepoType(self.repo_type_combo.currentData() or HfRepoType.MODEL.value),
                    selection_mode=mode,
                    filenames=tuple(self._split_list(self.files_edit.text())) if mode is HfSelectionMode.EXACT else (),
                    include=tuple(self._split_list(self.include_edit.text())) if mode is HfSelectionMode.PATTERNS else (),
                    exclude=tuple(self._split_list(self.exclude_edit.text())) if mode is HfSelectionMode.PATTERNS else (),
                    revision=self.revision_edit.text().strip(),
                    local_dir=local_dir,
                    cache_dir=cache_dir,
                    force_download=self.force_check.isChecked(),
                    max_workers=self.workers_spin.value() if self.workers_check.isChecked() else None,
                ),
                "",
            )
        except ValueError as error:
            return None, str(error)

    def _update_preview(self, *_args) -> None:
        request, error = self._build_request()
        if request is None:
            self.command_preview.setText(f"Command Preview: {error or 'fill in the request'}")
            return
        caps = self._capabilities or HfCliCapabilities(path="<hf>")
        try:
            argv = build_download_argv(caps, request)
        except HfCliError as error:
            self.command_preview.setText(f"Command Preview: {error}")
            return
        text = f"Command Preview:\n{render_command_line(argv)}"
        if self._capabilities is not None and not self._capabilities.dry_run:
            text += "\n(installed hf CLI: no --dry-run — Preview Download is disabled)"
        self.command_preview.setText(text)

    def _preview_download(self) -> None:
        request, error = self._build_request()
        if request is None:
            self.preview_box.setPlainText(error or "Fill in the request first.")
            return
        if not self.service.dry_run_supported:
            self.preview_box.setPlainText("The installed hf CLI does not support --dry-run; Preview Download is disabled.")
            return
        self._previewing = True
        self.preview_button.setEnabled(False)
        self.preview_box.setPlainText("Running dry-run preview…")
        self.post_status.setText("Previewing…")
        try:
            self.service.preview(request)
        except HfCliError as error:
            self._on_preview_finished(HfDryRunReport(raw=str(error)))
        except Exception as error:  # noqa: BLE001 - surface any spawn failure in the pane
            self._on_preview_finished(HfDryRunReport(raw=str(error)))

    def _on_preview_finished(self, report: HfDryRunReport) -> None:
        self._previewing = False
        if self._capabilities is not None:
            self.preview_button.setEnabled(self._capabilities.dry_run)
        if report.exit_code != 0 and not report.parsed:
            self.preview_box.setPlainText(redact_secrets(f"Preview failed (exit code {report.exit_code}):\n{report.raw}"))
            self.post_status.setText(f"Preview failed (exit code {report.exit_code}).")
            return
        if not report.parsed:
            self.preview_box.setPlainText(redact_secrets(report.raw or "(no dry-run output)"))
            self.post_status.setText("Dry-run output was not machine-readable; showing raw output.")
            return
        lines = [f"Total files: {report.total_files}   ·   To transfer: {report.transfer_files}   ·   Transfer size: {report.transfer_text or 'n/a'}"]
        for item in report.files:
            lines.append(f"{item.filename}  —  {item.size_text or 'already available'}  ({'will download' if item.will_download else 'already available'})")
        self.preview_box.setPlainText("\n".join(lines))
        self.post_status.setText(f"Preview: {report.transfer_files}/{report.total_files} files to transfer.")

    # ------------------------------------------------------------------ queue
    def _add_to_queue(self) -> None:
        request, error = self._build_request()
        if request is None:
            self.post_status.setText(error or "Fill in the request first.")
            return
        self._persist()
        job_id = self.service.request_download(request)
        job = self.service.job(job_id)
        self.post_status.setText(f"Queued: {job.request.describe()}")

    def _refresh_table(self) -> None:
        self.queue_table.setRowCount(0)
        for job in self.service.jobs():
            row = self.queue_table.rowCount()
            self.queue_table.insertRow(row)
            request = job.request
            values = [
                request.repo_id,
                request.repo_type.value,
                request.selection_summary(),
                request.target_label,
                job.state.value,
                job.result.detail if job.result else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, job.id if column == 0 else None)
                self.queue_table.setItem(row, column, item)
        self.queue_table.resizeColumnsToContents()

    def _selected_job_id(self) -> str | None:
        row = self.queue_table.currentRow()
        if row < 0:
            return None
        item = self.queue_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _remove_selected(self) -> None:
        job_id = self._selected_job_id()
        if not job_id:
            return
        if not self.service.remove_queued(job_id):
            self.post_status.setText("Only queued (not running, not finished) jobs can be removed; they stay in history.")
        else:
            self.post_status.setText("Removed queued job.")

    def _retry_selected(self) -> None:
        job_id = self._selected_job_id()
        if job_id and not self.service.retry(job_id):
            self.post_status.setText("Only failed or cancelled jobs can be retried.")

    def _move_selected(self, delta: int) -> None:
        job_id = self._selected_job_id()
        if job_id and not self.service.move_queued(job_id, delta):
            self.post_status.setText("Only queued jobs can be reordered (not the active download).")

    def _on_job_output(self, job_id: str, line: str) -> None:
        job = self.service.job(job_id)
        label = job.request.repo_id if job else job_id
        self.console_box.appendPlainText(f"[{label}] {redact_secrets(line)}")

    def _on_job_finished(self, job_id: str, result: HfDownloadResult) -> None:
        self._last_result = result
        self.post_status.setText(f"{job_id} {result.detail}")
        if result.ok and result.request.refreshes_selectors:
            self.folders_changed.emit([result.request.target.value])
        self._update_post_buttons()

    # ------------------------------------------------------------------ post download
    def _update_post_buttons(self) -> None:
        result = self._last_result
        for target in HfTarget:
            if target in self.use_as_buttons:
                self.use_as_buttons[target].setEnabled(False)
        if result is None or not result.ok or not result.new_files:
            self.open_folder_button.setEnabled(False)
            self.copy_path_button.setEnabled(False)
            return
        folder = result.request.local_dir
        self.open_folder_button.setEnabled(bool(folder and Path(folder).is_dir()))
        self.copy_path_button.setEnabled(bool(folder))
        if result.request.target in _CANDIDATE_SUFFIXES:
            suffixes = _CANDIDATE_SUFFIXES[result.request.target]
            candidates = [name for name in result.new_files if name.lower().endswith(suffixes)]
            if len(candidates) == 1:
                self.use_as_buttons[result.request.target].setEnabled(True)

    def _open_folder(self) -> None:
        result = self._last_result
        if result and result.request.local_dir:
            import platform

            folder = result.request.local_dir
            if platform.system() == "Windows":
                os.startfile(folder)  # noqa: S606 - user-chosen folder
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])

    def _copy_path(self) -> None:
        from PySide6.QtWidgets import QApplication

        result = self._last_result
        if result and result.request.local_dir:
            QApplication.clipboard().setText(result.request.local_dir)
            self.post_status.setText(f"Copied: {result.request.local_dir}")

    def _use_selected_candidate(self, flag: str) -> None:
        result = self._last_result
        if result is None or not result.request.local_dir:
            return
        if result.request.target not in _CANDIDATE_SUFFIXES:
            return
        suffixes = _CANDIDATE_SUFFIXES[result.request.target]
        candidates = [name for name in result.new_files if name.lower().endswith(suffixes)]
        if len(candidates) != 1:
            return
        self.use_requested.emit(flag, str(Path(result.request.local_dir) / candidates[0]))
