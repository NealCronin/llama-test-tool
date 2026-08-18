from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from app.models.memory import MemoryTestResult


def _gib(mib: float) -> str:
    return f"{mib / 1024:.2f} GiB"


def _item(mib: float) -> QTableWidgetItem:
    item = QTableWidgetItem(_gib(mib))
    item.setToolTip(f"{mib:.0f} MiB")
    return item


class MemoryResultsDialog(QDialog):
    apply_requested = Signal(tuple)

    def __init__(self, result: MemoryTestResult, parent=None) -> None:
        super().__init__(parent)
        self.result = result
        self.setWindowTitle("Memory Test Results")
        self.resize(860, 680)
        layout = QVBoxLayout(self)
        if not result.success:
            layout.addWidget(QLabel(f"<h2>Memory test failed</h2><p>{result.error or 'llama-fit-params did not return usable results.'}</p><p>Estimate exit: {result.exit_code}; fit exit: {result.fit_exit_code}</p>"))
        else:
            layout.addWidget(QLabel("<h2>Memory Estimate</h2>"))
            statuses = {
                "fitted": "Fitted parameters differ from the requested command.",
                "unchanged": "llama-fit-params reported no fitting changes.",
                "returned": "llama-fit-params returned parameters; their effect versus defaults is unknown.",
                "unknown": "No safely parseable fitted parameter line was returned.",
            }
            layout.addWidget(QLabel(statuses[result.fit_status]))
            self._add_summary(layout)
            self._add_scope(layout)
            self._add_devices(layout)
            self._add_fit_result(layout)
            self._add_details(layout)
        self._add_raw_output(layout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _add_summary(self, layout: QVBoxLayout) -> None:
        assert self.result.breakdown is not None
        breakdown = self.result.breakdown
        panel = QWidget()
        form = QFormLayout(panel)
        values = (
            ("Model Weights", breakdown.total_model_mib),
            ("Context / KV Cache", breakdown.total_context_mib),
            ("Compute Buffers", breakdown.total_compute_mib),
            ("Estimated Self", breakdown.total_self_mib),
        )
        for label, mib in values:
            value = QLabel(_gib(mib))
            value.setToolTip(f"{mib:.0f} MiB")
            form.addRow(label, value)
        layout.addWidget(panel)

    def _add_scope(self, layout: QVBoxLayout) -> None:
        if not self.result.sidecars:
            return
        sidecars = "<br>".join(f"{label}: <code>{path}</code>" for label, path in self.result.sidecars)
        layout.addWidget(QLabel(
            "<b>Estimate scope: main model memory only.</b> Additional sidecar memory may not be included:<br>" + sidecars
        ))

    def _add_devices(self, layout: QVBoxLayout) -> None:
        assert self.result.breakdown is not None
        layout.addWidget(QLabel("<h3>Memory Breakdown</h3>"))
        table = QTableWidget(len(self.result.breakdown.devices), 5)
        table.setHorizontalHeaderLabels(["Device", "Model", "Context", "Compute", "Total"])
        for row, device in enumerate(self.result.breakdown.devices):
            table.setItem(row, 0, QTableWidgetItem(device.device_name))
            table.setItem(row, 1, _item(device.model_mib))
            table.setItem(row, 2, _item(device.context_mib))
            table.setItem(row, 3, _item(device.compute_mib))
            table.setItem(row, 4, _item(device.self_mib if device.self_mib is not None else device.categorized_mib))
        table.resizeColumnsToContents()
        table.setMinimumHeight(min(240, 58 + len(self.result.breakdown.devices) * 30))
        layout.addWidget(table)

    def _add_fit_result(self, layout: QVBoxLayout) -> None:
        layout.addWidget(QLabel("<h3>Automatic Fitted Parameters</h3>"))
        if self.result.fit_error:
            layout.addWidget(QLabel(f"<b>Unavailable:</b> {self.result.fit_error} The memory estimate above is still valid."))
            return
        if self.result.fitted_arguments:
            fitted = " ".join(self.result.fitted_arguments)
            layout.addWidget(QLabel(f"Fitted arguments: <code>{fitted}</code>"))
            if self.result.fit_status == "fitted":
                apply = QPushButton("Apply Fitted Parameters")
                apply.clicked.connect(lambda: self.apply_requested.emit(self.result.fitted_arguments))
                layout.addWidget(apply)
        else:
            layout.addWidget(QLabel("No safely parseable fitted argument line was returned."))

    def _add_details(self, layout: QVBoxLayout) -> None:
        toggle = QToolButton(text="Show Memory Test Details", checkable=True)
        details = QPlainTextEdit(readOnly=True)
        sent = " ".join(self.result.requested_argv) or "(none)"
        skipped = "\n".join(self.result.skipped_arguments) or "(none)"
        details.setPlainText(f"Arguments sent to llama-fit-params:\n{sent}\n\nArguments skipped:\n{skipped}")
        details.setVisible(False)
        toggle.toggled.connect(details.setVisible)
        toggle.toggled.connect(lambda checked: toggle.setText("Hide Memory Test Details" if checked else "Show Memory Test Details"))
        layout.addWidget(toggle)
        layout.addWidget(details)

    def _add_raw_output(self, layout: QVBoxLayout) -> None:
        toggle = QToolButton(text="Show Raw llama-fit-params Output", checkable=True)
        raw = QPlainTextEdit(readOnly=True)
        raw.setPlainText(self.result.raw_output)
        raw.setVisible(False)
        toggle.toggled.connect(raw.setVisible)
        toggle.toggled.connect(lambda checked: toggle.setText("Hide Raw llama-fit-params Output" if checked else "Show Raw llama-fit-params Output"))
        copy = QPushButton("Copy Raw Output")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.result.raw_output))
        row = QHBoxLayout()
        row.addWidget(toggle)
        row.addWidget(copy)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(raw)
