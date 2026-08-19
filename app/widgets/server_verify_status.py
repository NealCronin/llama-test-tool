"""Compact staged status panel for the Server Test workflow.

Displays the four verification stages (Process / Ready / API / Inference)
through the service's stage_changed events and a summary once verification
completes. Purely presentational — all decisions live in
ServerVerificationService.
"""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

_GLYPHS = {"pending": "○", "running": "…", "passed": "✓", "failed": "✗", "skipped": "–"}

_STAGES = (
    ("process", "Process"),
    ("ready", "Ready"),
    ("api", "API"),
    ("inference", "Inference"),
)


class ServerVerifyStatusPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.title = QLabel("<b>Test Server</b> — idle")
        self.title.setStyleSheet("color: #f9fafb; background: #1f2937; padding: 4px 8px; border-radius: 4px;")
        layout.addWidget(self.title)
        grid = QGridLayout()
        self._rows: dict[str, QLabel] = {}
        for column, (key, label) in enumerate(_STAGES):
            row = QLabel(f"{_GLYPHS['pending']} {label}")
            row.setStyleSheet("color: #9ca3af; padding: 2px 8px;")
            grid.addWidget(row, 0, column)
            self._rows[key] = row
        layout.addLayout(grid)
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #d1d5db; padding: 2px 8px;")
        layout.addWidget(self._detail)

    def reset(self) -> None:
        self.title.setText("<b>Test Server</b> — idle")
        for key, row in self._rows.items():
            row.setText(f"{_GLYPHS['pending']} {dict(_STAGES)[key]}")
            row.setStyleSheet("color: #9ca3af; padding: 2px 8px;")
        self._detail.setText("")

    def handle_stage(self, stage: str, status: str, detail: str) -> None:
        if stage == "run" and status == "cancelled":
            self.title.setText("<b>Test Server</b> — cancelled")
            self._detail.setText(detail)
            return
        if stage not in self._rows:
            return
        self._rows[stage].setText(f"{_GLYPHS.get(status, '○')} {dict(_STAGES)[stage]}")
        color = {
            "pending": "#9ca3af",
            "running": "#d97706",
            "passed": "#15803d",
            "failed": "#b91c1c",
            "skipped": "#6b7280",
        }[status]
        self._rows[stage].setStyleSheet(f"color: {color}; padding: 2px 8px;")
        if detail:
            self._detail.setText(detail)

    def show_result(self, result, process_running: bool) -> None:
        if result.failed_stage:
            self.title.setText("<b>Test Server</b> — failed at " + result.failed_stage.title())
            running = "The server process is still running — Stop it when you are done." if process_running else ""
            self._detail.setText(f"{result.error_detail}\n{running}".strip())
            return
        if result.skipped_stage == "inference":
            self.title.setText("<b>Test Server</b> — verified (inference skipped)")
            self._detail.setText(result.error_detail)
            return
        if result.skipped_stage == "ready":
            self.title.setText("<b>Test Server</b> — transport not verified")
            self._detail.setText(result.error_detail)
            return
        self.title.setText("<b>Test Server</b> — verified")
        lines = [f"Model: {', '.join(result.model_ids)}" if result.model_ids else "Model: (none reported)"]
        if result.generated_text:
            lines.append(f"Generated: {result.generated_text.strip()[:80]}")
        if result.prompt_tokens is not None:
            lines.append(f"Prompt: {result.prompt_tokens} tokens")
        if result.completion_tokens is not None:
            lines.append(f"Generated: {result.completion_tokens} tokens")
        if result.generation_tps is not None:
            lines.append(f"Speed: {result.generation_tps:.1f} t/s")
        if result.ready_ms is not None:
            lines.append(f"Ready: {result.ready_ms / 1000:.1f} s")
        if result.inference_ms is not None:
            lines.append(f"Inference: {result.inference_ms / 1000:.2f} s")
        lines.append("Server process remains running — use Stop to end it.")
        self._detail.setText("\n".join(lines))