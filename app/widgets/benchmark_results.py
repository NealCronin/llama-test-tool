from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget

from app.models.benchmark import BenchmarkResult, BenchmarkTest


class BenchmarkResultsDialog(QDialog):
    def __init__(self, result: BenchmarkResult, parent=None) -> None:
        super().__init__(parent)
        self.result = result
        self.setWindowTitle("Benchmark Results")
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>llama-bench inference benchmark</h2><p>Core inference performance only; this is not llama-server request throughput.</p>"))
        if not result.success:
            layout.addWidget(QLabel(f"<h3>Benchmark failed</h3><p>{result.error or 'No usable benchmark results were returned.'}</p>"))
        else:
            self._add_highlights(layout)
            self._add_table(layout)
            self._add_configuration(layout)
        self._add_details(layout)
        self._add_raw_output(layout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_highlights(self, layout: QVBoxLayout) -> None:
        cards = QHBoxLayout()
        for label, test in (("Prompt Processing", next((test for test in self.result.tests if test.test_type == "pp"), None)), ("Text Generation", next((test for test in self.result.tests if test.test_type == "tg"), None))):
            card = QLabel(f"<h3>{label}</h3><b>{test.tokens_per_second:,.1f} t/s</b><br>± {test.stddev_tokens_per_second:,.1f}" if test else f"<h3>{label}</h3>Not produced")
            card.setStyleSheet("background: #1f2937; color: #f9fafb; padding: 10px; border-radius: 4px;")
            cards.addWidget(card)
        layout.addLayout(cards)

    def _add_table(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("<h3>Tests</h3>"))
        table = QTableWidget(len(self.result.tests), 4)
        table.setHorizontalHeaderLabels(["Test", "Tokens", "t/s", "±"])
        for row, test in enumerate(self.result.tests):
            token_count = test.prompt_tokens if test.test_type == "pp" else test.generation_tokens
            table.setItem(row, 0, QTableWidgetItem(test.test_type))
            table.setItem(row, 1, QTableWidgetItem(str(token_count)))
            table.setItem(row, 2, QTableWidgetItem(f"{test.tokens_per_second:,.2f}"))
            table.setItem(row, 3, QTableWidgetItem(f"{test.stddev_tokens_per_second:,.2f}"))
        table.resizeColumnsToContents()
        table.setMaximumHeight(200)
        layout.addWidget(table)

    def _add_configuration(self, layout: QVBoxLayout) -> None:
        test: BenchmarkTest = self.result.tests[0]
        panel = QWidget()
        form = QFormLayout(panel)
        form.addRow("Backend", QLabel(test.backend))
        form.addRow("GPU", QLabel(test.gpu_info))
        form.addRow("Batch / UBatch", QLabel(f"{test.batch} / {test.ubatch}"))
        form.addRow("KV", QLabel(f"{test.cache_type_k} / {test.cache_type_v}"))
        form.addRow("GPU Layers", QLabel(str(test.gpu_layers)))
        form.addRow("CPU MoE Layers", QLabel(str(test.cpu_moe_layers)))
        form.addRow("Split Mode", QLabel(test.split_mode))
        form.addRow("Tensor Split", QLabel(test.tensor_split))
        layout.addWidget(panel)

    def _add_details(self, layout: QVBoxLayout) -> None:
        toggle = QToolButton(text="Show Benchmark Details", checkable=True)
        details = QPlainTextEdit(readOnly=True)
        details.setPlainText("Arguments translated:\n" + ("\n".join(self.result.translated_arguments) or "(none)") + "\n\nArguments sent to llama-bench:\n" + (" ".join(self.result.sent_argv) or "(none)") + "\n\nArguments skipped:\n" + ("\n".join(self.result.skipped_arguments) or "(none)") + "\n\nWarnings:\n" + ("\n".join(self.result.warnings) or "(none)"))
        details.setVisible(False)
        toggle.toggled.connect(details.setVisible)
        toggle.toggled.connect(lambda checked: toggle.setText("Hide Benchmark Details" if checked else "Show Benchmark Details"))
        layout.addWidget(toggle)
        layout.addWidget(details)

    def _add_raw_output(self, layout: QVBoxLayout) -> None:
        toggle = QToolButton(text="Show Raw llama-bench Output", checkable=True)
        raw = QPlainTextEdit(readOnly=True)
        raw.setPlainText(self.result.raw_output)
        raw.setVisible(False)
        toggle.toggled.connect(raw.setVisible)
        toggle.toggled.connect(lambda checked: toggle.setText("Hide Raw llama-bench Output" if checked else "Show Raw llama-bench Output"))
        copy_results = QPushButton("Copy Results")
        copy_results.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._result_text()))
        copy_raw = QPushButton("Copy Raw Output")
        copy_raw.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.result.raw_output))
        buttons = QHBoxLayout()
        buttons.addWidget(toggle); buttons.addWidget(copy_results); buttons.addWidget(copy_raw); buttons.addStretch()
        layout.addLayout(buttons)
        layout.addWidget(raw)

    def _result_text(self) -> str:
        return "\n".join(f"{test.test_type} {test.prompt_tokens or test.generation_tokens}: {test.tokens_per_second:.2f} ± {test.stddev_tokens_per_second:.2f} t/s" for test in self.result.tests)
