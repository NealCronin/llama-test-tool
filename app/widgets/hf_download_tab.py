from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QRadioButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.models.hf_download import HfDownloadRequest, HfTarget, TARGET_LABELS, TARGET_SETTING_KEYS
from app.settings import AppSettings
from app.services.hf_cli_service import HfCliError, HfCliService, build_download_argv, find_hf_cli, render_command_line

_QUEUE_COLUMNS = ("Repository", "Target", "State", "Result")


class HfDownloadTab(QWidget):
    """GUI/process manager around the installed `hf` CLI for model acquisition."""
    folders_changed = Signal(list)  # HfTarget values whose folders gained new files

    def __init__(self, settings: AppSettings, service: HfCliService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.service = service
        self._rows: dict[str, int] = {}
        self._changed_targets: set = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        repo_group = QGroupBox("Repository")
        repo_form = QFormLayout(repo_group)
        self.repo_edit = QLineEdit(repo_group)
        self.repo_edit.setPlaceholderText("owner/repo, e.g. ggml-org/gemma-3-4b-it-GGUF")
        self.revision_edit = QLineEdit(repo_group)
        self.revision_edit.setPlaceholderText("Branch, tag, or commit (optional)")
        repo_form.addRow("Repo ID", self.repo_edit)
        repo_form.addRow("Revision", self.revision_edit)

        selection_group = QGroupBox("File selection")
        selection_layout = QVBoxLayout(selection_group)
        self.exact_radio = QRadioButton("Exact file names (one per line)", selection_group)
        self.exact_radio.setChecked(True)
        self.include_radio = QRadioButton("Include glob patterns (one per line)", selection_group)
        self.files_edit = QPlainTextEdit(selection_group)
        self.files_edit.setPlaceholderText("model-Q4_K_M.gguf\nmmproj-model-f16.gguf")
        self.files_edit.setMaximumBlockCount(12)
        self.exclude_edit = QLineEdit(selection_group)
        self.exclude_edit.setPlaceholderText("Exclude globs, comma separated (optional), e.g. *.safetensors,README.md")
        self.target_combo = QComboBox(selection_group)
        for target in HfTarget:
            self.target_combo.addItem(TARGET_LABELS[target], target)
        self.target_path_label = QLabel(selection_group)
        self.token_edit = QLineEdit(selection_group)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("Optional; not stored; HF_TOKEN environment variable is also honored")
        self.workers_spin = QSpinBox(selection_group)
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(8)
        selection_form = QFormLayout()
        selection_form.addRow(self.exact_radio)
        selection_form.addRow(self.include_radio)
        selection_form.addRow("Files / patterns", self.files_edit)
        selection_form.addRow("Exclude", self.exclude_edit)
        selection_form.addRow("Destination folder", self.target_combo)
        selection_form.addRow("", self.target_path_label)
        selection_form.addRow("HF token", self.token_edit)
        selection_form.addRow("Max workers", self.workers_spin)
        selection_layout.addLayout(selection_form)
        top.addWidget(repo_group)
        top.addWidget(selection_group, 1)
        layout.addLayout(top)

        self.hf_status = QLabel(self)
        layout.addWidget(self.hf_status)
        self._update_hf_status()

        preview_title = QLabel("Command to run (preview)", self)
        preview_title.setStyleSheet("color: #6b7280;")
        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMaximumBlockCount(200)
        layout.addWidget(preview_title)
        layout.addWidget(self.preview)

        actions = QHBoxLayout()
        self.start_button = QPushButton("Start download", self)
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.queue_table = QTableWidget(0, len(_QUEUE_COLUMNS), self)
        self.queue_table.setHorizontalHeaderLabels(_QUEUE_COLUMNS)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.queue_table.setMaximumHeight(140)
        layout.addWidget(self.queue_table)

        self.console = QPlainTextEdit(self)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(2000)
        layout.addWidget(self.console, 1)

        for widget in (self.repo_edit, self.revision_edit, self.files_edit, self.exclude_edit, self.token_edit):
            widget.textChanged.connect(self._update_preview)
        self.workers_spin.valueChanged.connect(self._update_preview)
        for radio in (self.exact_radio, self.include_radio):
            radio.toggled.connect(self._update_preview)
        self.target_combo.currentIndexChanged.connect(self._update_target_path)
        self.target_combo.currentIndexChanged.connect(self._update_preview)
        self.service.output.connect(self._append_output)
        self.service.state_changed.connect(self._state_changed)
        self.service.finished.connect(self._finished)
        self.service.all_finished.connect(self._all_finished)
        self._update_target_path()
        self._update_preview()

    # ------------------------------------------------------------------ status
    def _update_hf_status(self) -> None:
        try:
            info = find_hf_cli()
            self.hf_status.setText(f"hf CLI: {info.path} (huggingface_hub {info.hub_version})")
            self.hf_status.setStyleSheet("color: #6b7280;")
        except HfCliError as error:
            self.hf_status.setText(str(error))
            self.hf_status.setStyleSheet("color: #b91c1c;")

    def _target_path(self) -> Path | None:
        target = self.target_combo.currentData()
        key = TARGET_SETTING_KEYS[target]
        value = getattr(self.settings, key, "")
        return Path(value) if value else None

    def _update_target_path(self) -> None:
        path = self._target_path()
        if path is None:
            self.target_path_label.setText("Not set — choose it in Settings.")
            self.target_path_label.setStyleSheet("color: #b91c1c;")
        else:
            self.target_path_label.setText(str(path))
            self.target_path_label.setStyleSheet("color: #6b7280;")

    # ------------------------------------------------------------------ request
    def _selection_lines(self) -> list[str]:
        return [line.strip() for line in self.files_edit.toPlainText().splitlines() if line.strip()]

    def _exclude_list(self) -> list[str]:
        return [part.strip() for part in self.exclude_edit.text().split(",") if part.strip()]

    def current_request(self) -> HfDownloadRequest | None:
        repo = self.repo_edit.text().strip()
        if not repo:
            return None
        lines = self._selection_lines()
        target = self.target_combo.currentData()
        return HfDownloadRequest(
            repo_id=repo,
            target=target,
            filenames=lines if self.exact_radio.isChecked() else [],
            include=[] if self.exact_radio.isChecked() else lines,
            exclude=self._exclude_list(),
            revision=self.revision_edit.text(),
            token=self.token_edit.text(),
            max_workers=self.workers_spin.value(),
        )

    def _update_preview(self, *_args) -> None:
        request = self.current_request()
        if request is None:
            self.preview.setPlainText("Enter a repo ID and a file selection to preview the command.")
            return
        path = self._target_path()
        if path is None:
            self.preview.setPlainText("Set the destination folder in Settings before previewing or starting a download.")
            return
        try:
            argv = build_download_argv("<hf>", request, path)
        except Exception as error:  # preview must never raise
            self.preview.setPlainText(f"Preview unavailable: {error}")
            return
        shown = render_command_line(argv)
        if request.token:
            shown = shown.replace(request.token, "<token>")
        self.preview.setPlainText(shown)

    # ------------------------------------------------------------------ running
    def start(self) -> None:
        request = self.current_request()
        if request is None:
            self._note("Enter a repo ID (owner/repo) first.")
            return
        if not request.filenames and not request.include:
            self._note("Select at least one file name or include pattern.")
            return
        path = self._target_path()
        if path is None:
            self._note("The destination folder is not set. Open Settings and choose the folder for this download type.")
            return
        try:
            find_hf_cli()
        except HfCliError as error:
            self._note(str(error))
            return
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        self.queue_table.setItem(row, 0, QTableWidgetItem(request.repo_id))
        self.queue_table.setItem(row, 1, QTableWidgetItem(TARGET_LABELS[request.target]))
        self.queue_table.setItem(row, 2, QTableWidgetItem("Queued"))
        self.queue_table.setItem(row, 3, QTableWidgetItem(""))
        self._rows[self.service.enqueue(request, path)] = row
        self._set_running(True)

    def _stop(self) -> None:
        self.service.stop()

    def _set_running(self, running: bool) -> None:
        self.stop_button.setEnabled(running)
        self.start_button.setEnabled(not running)

    def _note(self, message: str) -> None:
        self.console.appendPlainText(message)

    def _append_output(self, request_id: str, line: str) -> None:
        row = self._rows.get(request_id)
        repo_item = self.queue_table.item(row, 0) if row is not None else None
        prefix = f"[{repo_item.text()}] " if repo_item is not None else ""
        self.console.appendPlainText(f"{prefix}{line}")

    def _state_changed(self, request_id: str, state: str) -> None:
        row = self._rows.get(request_id)
        if row is None:
            return
        item = self.queue_table.item(row, 2)
        if item is not None:
            item.setText(state)

    def _finished(self, request_id: str, result) -> None:
        row = self._rows.get(request_id)
        if row is not None:
            item = self.queue_table.item(row, 3)
            if item is not None:
                item.setText("OK" if result.success else "Failed")
        if result.success:
            self._changed_targets.add(result.request.target)

    def _all_finished(self, had_success: bool) -> None:
        self._set_running(False)
        if self._changed_targets:
            self.folders_changed.emit(sorted(self._changed_targets, key=list(HfTarget).index))
            self._changed_targets.clear()
        self._update_hf_status()
