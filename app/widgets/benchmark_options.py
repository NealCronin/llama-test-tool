from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit

from app.models.benchmark import BenchmarkOptions
from app.settings import AppSettings


class BenchmarkOptionsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Benchmark Options")
        form = QFormLayout(self)
        self.prompt = QLineEdit(str(settings.benchmark_prompt_tokens))
        self.generation = QLineEdit(str(settings.benchmark_generation_tokens))
        self.repetitions = QLineEdit(str(settings.benchmark_repetitions))
        self.depth = QLineEdit(str(settings.benchmark_context_depth))
        self.delay = QLineEdit(str(settings.benchmark_delay))
        self.no_warmup = QCheckBox("Skip warmup runs")
        self.no_warmup.setChecked(settings.benchmark_no_warmup)
        form.addRow("Prompt tokens (-p)", self.prompt)
        form.addRow("Generation tokens (-n)", self.generation)
        form.addRow("Repetitions (-r)", self.repetitions)
        form.addRow("Context depth (-d)", self.depth)
        form.addRow("Delay seconds (--delay)", self.delay)
        form.addRow(self.no_warmup)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        form.addRow(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def options(self) -> BenchmarkOptions:
        try:
            values = [int(field.text()) for field in (self.prompt, self.generation, self.repetitions, self.depth, self.delay)]
        except ValueError as error:
            raise ValueError("Benchmark options must be whole numbers.") from error
        if any(value < 0 for value in values) or values[2] < 1:
            raise ValueError("Prompt, generation, depth, and delay must be non-negative; repetitions must be at least one.")
        return BenchmarkOptions(*values, self.no_warmup.isChecked())

    def save_to(self, settings: AppSettings) -> BenchmarkOptions:
        options = self.options()
        settings.benchmark_prompt_tokens = options.prompt_tokens
        settings.benchmark_generation_tokens = options.generation_tokens
        settings.benchmark_repetitions = options.repetitions
        settings.benchmark_context_depth = options.context_depth
        settings.benchmark_delay = options.delay
        settings.benchmark_no_warmup = options.no_warmup
        settings.save()
        return options
