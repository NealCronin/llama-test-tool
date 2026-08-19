from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit


class ModelDialog(QDialog):
    """Create a new llama-swap model entry from a tested configuration."""

    def __init__(self, suggested_id: str, context_size: str = "", has_mmproj: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add to llama-swap")
        form = QFormLayout(self)
        self.model_id = QLineEdit(suggested_id)
        self.display_name = QLineEdit()
        self.description = QLineEdit()
        self.use_model_name = QLineEdit()
        self.check_endpoint = QLineEdit("/health")
        self.inherit_ttl = QCheckBox("Inherit global TTL")
        self.inherit_ttl.setChecked(True)
        self.ttl = QLineEdit()
        self.ttl.setEnabled(False)
        self.inherit_unload = QCheckBox("Inherit global unload timeout")
        self.inherit_unload.setChecked(True)
        self.unload = QLineEdit()
        self.unload.setEnabled(False)
        self.input_text = QCheckBox("Input text")
        self.input_text.setChecked(True)
        self.input_image = QCheckBox("Input image")
        self.input_image.setChecked(has_mmproj)
        self.input_audio = QCheckBox("Input audio")
        self.output_text = QCheckBox("Output text")
        self.output_text.setChecked(True)
        self.output_image = QCheckBox("Output image")
        self.output_audio = QCheckBox("Output audio")
        self.tools = QCheckBox("Tools")
        self.reranker = QCheckBox("Reranker")
        self.context = QLineEdit(context_size)
        self.inherit_ttl.toggled.connect(lambda checked: self.ttl.setEnabled(not checked))
        self.inherit_unload.toggled.connect(lambda checked: self.unload.setEnabled(not checked))
        for label, widget in (("Model ID", self.model_id), ("Display name", self.display_name), ("Description", self.description), ("useModelName", self.use_model_name), ("checkEndpoint", self.check_endpoint), ("TTL override", self.ttl), ("Unload timeout override", self.unload), ("Context capability", self.context)):
            form.addRow(label, widget)
        form.addRow(self.inherit_ttl)
        form.addRow(self.inherit_unload)
        for widget in (self.input_text, self.input_image, self.input_audio, self.output_text, self.output_image, self.output_audio, self.tools, self.reranker):
            form.addRow(widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def metadata(self) -> dict[str, object]:
        try:
            ttl = None if self.inherit_ttl.isChecked() else int(self.ttl.text())
            unload = None if self.inherit_unload.isChecked() else int(self.unload.text())
            context = int(self.context.text()) if self.context.text().strip() else None
        except ValueError as error:
            raise ValueError("TTL, unload timeout, and context capability must be whole numbers.") from error
        if context is not None and context < 0:
            raise ValueError("Context capability cannot be negative.")
        # Empty in/out lists are omitted: the bundled schema requires at least one
        # capability per present list, and an empty block means "runtime defaults".
        inputs = [name for name, checked in (("text", self.input_text.isChecked()), ("image", self.input_image.isChecked()), ("audio", self.input_audio.isChecked())) if checked]
        outputs = [name for name, checked in (("text", self.output_text.isChecked()), ("image", self.output_image.isChecked()), ("audio", self.output_audio.isChecked())) if checked]
        capabilities: dict[str, object] = {}
        if inputs:
            capabilities["in"] = inputs
        if outputs:
            capabilities["out"] = outputs
        if self.tools.isChecked():
            capabilities["tools"] = True
        if self.reranker.isChecked():
            capabilities["reranker"] = True
        if context is not None:
            capabilities["context"] = context
        metadata: dict[str, object] = {}
        if capabilities:
            metadata["capabilities"] = capabilities
        if self.description.text().strip():
            metadata["description"] = self.description.text().strip()
        if self.use_model_name.text().strip():
            metadata["useModelName"] = self.use_model_name.text().strip()
        if self.check_endpoint.text().strip():
            metadata["checkEndpoint"] = self.check_endpoint.text().strip()
        if ttl is not None:
            metadata["ttl"] = ttl
        if unload is not None:
            metadata["unloadTimeout"] = unload
        return metadata
