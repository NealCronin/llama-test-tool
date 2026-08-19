from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QWidget

from app.models.command import Command
from app.services.flag_catalog import FlagCatalog
from app.services.file_scanner import scan_gguf_models


def current_value(command: Command, catalog: FlagCatalog | None, reference: str) -> str | None:
    """Resolve a logical flag reference (canonical name or any alias) to the current command value.

    The builder may store arguments under any alias spelling, so preset dialogs never depend on a
    particular spelling: the catalog maps the reference to its logical flag and the first value of
    the matching argument is returned.
    """
    if catalog is None:
        argument = command.find((reference,))
        return argument.values[0] if argument and argument.values else None
    spec = catalog.find(reference)
    if spec is None:
        return None
    argument = command.find(spec.aliases)
    return argument.values[0] if argument and argument.values else None


def has_flag(command: Command, catalog: FlagCatalog | None, reference: str) -> bool:
    if catalog is None:
        return command.has_flag((reference,))
    spec = catalog.find(reference)
    return command.has_flag(spec.aliases if spec is not None else (reference,))


class _PresetDialog(QDialog):
    def _buttons(self, form: QFormLayout) -> None:
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class ContextKvPresetDialog(_PresetDialog):
    def __init__(self, catalog, command: Command, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Context + KV Cache")
        form = QFormLayout(self)
        choices = catalog.find("--cache-type-k")
        values = choices.choices if choices and choices.choices else ("f32", "f16", "bf16", "q8_0", "q5_0", "q5_1", "q4_0", "q4_1", "iq4_nl")
        self.context = QLineEdit(current_value(command, catalog, "--ctx-size") or "131072")
        self.k_type = QComboBox(); self.k_type.addItems(values); self.k_type.setCurrentText(current_value(command, catalog, "--cache-type-k") or self.k_type.currentText())
        self.v_type = QComboBox(); self.v_type.addItems(values); self.v_type.setCurrentText(current_value(command, catalog, "--cache-type-v") or self.k_type.currentText())
        self.same = QCheckBox("Use same quant for K and V")
        self.same.setChecked(self.k_type.currentText() == self.v_type.currentText())
        self.draft = QCheckBox("Apply same types to draft KV cache")
        self.draft.setChecked(bool(current_value(command, catalog, "--cache-type-k-draft") or current_value(command, catalog, "--cache-type-v-draft")))
        self.same.toggled.connect(lambda checked: self.v_type.setEnabled(not checked))
        form.addRow("Context size", self.context)
        form.addRow("K cache type", self.k_type)
        form.addRow("V cache type", self.v_type)
        form.addRow(self.same)
        form.addRow(self.draft)
        self._buttons(form)

    def values(self) -> dict[str, list[str]]:
        try:
            context = str(int(self.context.text()))
        except ValueError as error:
            raise ValueError("Context size must be a whole number.") from error
        k_type = self.k_type.currentText()
        v_type = k_type if self.same.isChecked() else self.v_type.currentText()
        result = {"--ctx-size": [context], "--cache-type-k": [k_type], "--cache-type-v": [v_type]}
        if self.draft.isChecked():
            result.update({"--cache-type-k-draft": [k_type], "--cache-type-v-draft": [v_type]})
        return result


class DeviceSplitPresetDialog(_PresetDialog):
    def __init__(self, catalog, command: Command, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Device Split")
        form = QFormLayout(self)
        self.devices = QLineEdit(current_value(command, catalog, "--device") or "Vulkan0,CUDA0")
        self.mode = QComboBox(); self.mode.addItems(("none", "layer", "row", "tensor")); self.mode.setCurrentText(current_value(command, catalog, "--split-mode") or "layer")
        self.tensor_split = QLineEdit(current_value(command, catalog, "--tensor-split") or "2,1")
        self.main_gpu = QLineEdit(current_value(command, catalog, "--main-gpu") or "0")
        self.gpu_layers = QLineEdit(current_value(command, catalog, "--gpu-layers") or "all")
        self.all_moe_cpu = QCheckBox("Keep all MoE on CPU (-cmoe)")
        self.all_moe_cpu.setChecked(has_flag(command, catalog, "--cpu-moe"))
        self.cpu_moe_layers = QLineEdit(current_value(command, catalog, "--n-cpu-moe") or "")
        self.all_moe_cpu.toggled.connect(lambda checked: self.cpu_moe_layers.setEnabled(not checked))
        form.addRow("Devices", self.devices)
        form.addRow("Split mode", self.mode)
        form.addRow("Tensor split", self.tensor_split)
        form.addRow("Main GPU", self.main_gpu)
        form.addRow("GPU layers", self.gpu_layers)
        form.addRow(self.all_moe_cpu)
        form.addRow("First MoE layers on CPU (-ncmoe)", self.cpu_moe_layers)
        self._buttons(form)

    def values(self) -> dict[str, list[str]]:
        result = {
            "--device": [self.devices.text().strip()], "--split-mode": [self.mode.currentText()],
            "--tensor-split": [self.tensor_split.text().strip()], "--main-gpu": [self.main_gpu.text().strip()],
            "--gpu-layers": [self.gpu_layers.text().strip()],
        }
        if self.all_moe_cpu.isChecked():
            result["--cpu-moe"] = []
        else:
            layers = self.cpu_moe_layers.text().strip()
            if layers:
                try:
                    count = str(int(layers))
                except ValueError as error:
                    raise ValueError("CPU-MoE layer count must be a whole number.") from error
                if int(layers) < 0:
                    raise ValueError("CPU-MoE layer count cannot be negative.")
                result["--n-cpu-moe"] = [count]
        return result


class CustomMtpPresetDialog(_PresetDialog):
    def __init__(self, settings, catalog, command: Command, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom MTP / External Drafter")
        form = QFormLayout(self)
        self.drafter = QComboBox(); self.drafter.setEditable(True)
        self.drafter.addItem("Select drafter…", "")
        for path in scan_gguf_models(settings.drafters_folder):
            self.drafter.addItem(path.name, str(path))
        drafter_row = QWidget()
        drafter_layout = QHBoxLayout(drafter_row); drafter_layout.setContentsMargins(0, 0, 0, 0)
        drafter_layout.addWidget(self.drafter, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        drafter_layout.addWidget(browse)
        # Every real quant choice carries its value as item data so that
        # currentData() is non-empty exactly when an explicit draft type is selected.
        choices = catalog.find("--cache-type-k-draft")
        values = choices.choices if choices and choices.choices else ("f16", "bf16", "q8_0")
        self.k_type = QComboBox()
        self._draft_combo(self.k_type, values, current_value(command, catalog, "--cache-type-k-draft"))
        self.v_type = QComboBox()
        self._draft_combo(self.v_type, values, current_value(command, catalog, "--cache-type-v-draft"))
        self.n_max = QLineEdit(current_value(command, catalog, "--spec-draft-n-max") or "")
        self.p_min = QLineEdit(current_value(command, catalog, "--spec-draft-p-min") or "")
        if current_value(command, catalog, "--spec-draft-model"):
            self.drafter.setEditText(current_value(command, catalog, "--spec-draft-model"))
        form.addRow("Drafter", drafter_row)
        form.addRow("Draft K cache type (optional)", self.k_type)
        form.addRow("Draft V cache type (optional)", self.v_type)
        form.addRow("Draft maximum tokens (optional)", self.n_max)
        form.addRow("Draft minimum probability (optional)", self.p_min)
        self._buttons(form)

    @staticmethod
    def _draft_combo(combo: QComboBox, choices, current: str | None) -> None:
        combo.addItem("Model/default", "")
        for value in choices:
            combo.addItem(value, value)
        if current and combo.findData(current) < 0:
            combo.addItem(current, current)
        combo.setCurrentIndex(combo.findData(current) if current else 0)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose drafter model", self.drafter.currentData() or "", "GGUF files (*.gguf);;All files (*)")
        if path:
            index = self.drafter.findData(path)
            if index < 0:
                self.drafter.addItem(path, path)
                index = self.drafter.count() - 1
            self.drafter.setCurrentIndex(index)

    def values(self) -> dict[str, list[str]]:
        path = self.drafter.currentData() or self.drafter.currentText().strip()
        if not path:
            raise ValueError("Select or enter an external drafter model.")
        result = {"--spec-draft-model": [path], "--spec-type": ["draft-mtp"]}
        k_value = self.k_type.currentData()
        if k_value:
            result["--cache-type-k-draft"] = [str(k_value)]
        v_value = self.v_type.currentData()
        if v_value:
            result["--cache-type-v-draft"] = [str(v_value)]
        if self.n_max.text().strip():
            result["--spec-draft-n-max"] = [self.n_max.text().strip()]
        if self.p_min.text().strip():
            result["--spec-draft-p-min"] = [self.p_min.text().strip()]
        return result
