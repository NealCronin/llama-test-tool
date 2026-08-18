from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QWidget

from app.services.file_scanner import scan_gguf_models


class _PresetDialog(QDialog):
    def _buttons(self, form: QFormLayout) -> None:
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class ContextKvPresetDialog(_PresetDialog):
    def __init__(self, catalog, current: dict[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Context + KV Cache")
        form = QFormLayout(self)
        choices = catalog.find("--cache-type-k")
        values = choices.choices if choices and choices.choices else ("f32", "f16", "bf16", "q8_0", "q5_0", "q5_1", "q4_0", "q4_1", "iq4_nl")
        current = current or {}
        self.context = QLineEdit(current.get("--ctx-size", "131072"))
        self.k_type = QComboBox(); self.k_type.addItems(values); self.k_type.setCurrentText(current.get("--cache-type-k", self.k_type.currentText()))
        self.v_type = QComboBox(); self.v_type.addItems(values); self.v_type.setCurrentText(current.get("--cache-type-v", self.v_type.currentText()))
        self.same = QCheckBox("Use same quant for K and V")
        self.same.setChecked(self.k_type.currentText() == self.v_type.currentText())
        self.draft = QCheckBox("Apply same types to draft KV cache")
        self.draft.setChecked(bool(current.get("--cache-type-k-draft") or current.get("--cache-type-v-draft")))
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
    def __init__(self, current: dict[str, str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Device Split")
        form = QFormLayout(self)
        current = current or {}
        self.devices = QLineEdit(current.get("--device", "Vulkan0,CUDA0"))
        self.mode = QComboBox(); self.mode.addItems(("none", "layer", "row", "tensor")); self.mode.setCurrentText(current.get("--split-mode", "layer"))
        self.tensor_split = QLineEdit(current.get("--tensor-split", "2,1"))
        self.main_gpu = QLineEdit(current.get("--main-gpu", "0"))
        self.gpu_layers = QLineEdit(current.get("--gpu-layers", "all"))
        self.all_moe_cpu = QCheckBox("Keep all MoE on CPU (-cmoe)")
        self.all_moe_cpu.setChecked("--cpu-moe" in current)
        self.cpu_moe_layers = QLineEdit(current.get("--n-cpu-moe", ""))
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
        if self.all_moe_cpu.isChecked(): result["--cpu-moe"] = []
        elif self.cpu_moe_layers.text().strip(): result["--n-cpu-moe"] = [self.cpu_moe_layers.text().strip()]
        return result


class CustomMtpPresetDialog(_PresetDialog):
    def __init__(self, settings, catalog, current: dict[str, str] | None = None, parent=None) -> None:
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
        choices = catalog.find("--cache-type-k-draft")
        values = choices.choices if choices and choices.choices else ("f16", "bf16", "q8_0")
        self.k_type = QComboBox(); self.k_type.addItem("Model/default", ""); self.k_type.addItems(values)
        self.v_type = QComboBox(); self.v_type.addItem("Model/default", ""); self.v_type.addItems(values)
        self.n_max = QLineEdit(); self.p_min = QLineEdit()
        current = current or {}
        if current.get("--spec-draft-model"):
            self.drafter.setEditText(current["--spec-draft-model"])
        self.k_type.setCurrentText(current.get("--cache-type-k-draft", "Model/default"))
        self.v_type.setCurrentText(current.get("--cache-type-v-draft", "Model/default"))
        self.n_max.setText(current.get("--spec-draft-n-max", ""))
        self.p_min.setText(current.get("--spec-draft-p-min", ""))
        form.addRow("Drafter", drafter_row)
        form.addRow("Draft K cache type (optional)", self.k_type)
        form.addRow("Draft V cache type (optional)", self.v_type)
        form.addRow("Draft maximum tokens (optional)", self.n_max)
        form.addRow("Draft minimum probability (optional)", self.p_min)
        self._buttons(form)

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
        if self.k_type.currentData():
            result["--cache-type-k-draft"] = [self.k_type.currentText()]
        if self.v_type.currentData():
            result["--cache-type-v-draft"] = [self.v_type.currentText()]
        if self.n_max.text().strip():
            result["--spec-draft-n-max"] = [self.n_max.text().strip()]
        if self.p_min.text().strip():
            result["--spec-draft-p-min"] = [self.p_min.text().strip()]
        return result
