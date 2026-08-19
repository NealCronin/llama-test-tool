import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.models.command import Command, CommandArgument
from app.services.flag_catalog import FlagCatalog
from app.settings import AppSettings
from app.widgets.guided_presets import ContextKvPresetDialog, CustomMtpPresetDialog, DeviceSplitPresetDialog, current_value, has_flag

QApplication.instance() or QApplication([])
BUNDLED_CATALOG = Path(__file__).resolve().parent.parent / "data" / "llama_server_flags.json"


def catalog() -> FlagCatalog:
    return FlagCatalog.load_bundled(BUNDLED_CATALOG)


def test_current_value_resolves_canonical_and_alias_spellings():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"], "model"),
        CommandArgument("-ctkd", ["bf16"]),
        CommandArgument("--spec-draft-type-v", ["q8_0"]),
    ])
    specs = catalog()
    assert current_value(command, specs, "--cache-type-k-draft") == "bf16"
    assert current_value(command, specs, "--spec-draft-type-k") == "bf16"
    assert current_value(command, specs, "--cache-type-v-draft") == "q8_0"
    assert current_value(command, specs, "--ctx-size") is None
    assert has_flag(command, specs, "--cache-type-k-draft")
    assert not has_flag(command, specs, "--ctx-size")


def test_custom_mtp_default_emits_no_draft_kv_flags():
    dialog = CustomMtpPresetDialog(AppSettings(), catalog(), Command())
    dialog.drafter.setEditText("draft.gguf")
    values = dialog.values()
    assert values["--spec-draft-model"] == ["draft.gguf"]
    assert values["--spec-type"] == ["draft-mtp"]
    assert "--cache-type-k-draft" not in values
    assert "--cache-type-v-draft" not in values


def test_custom_mtp_bf16_k_emits_draft_k_option():
    dialog = CustomMtpPresetDialog(AppSettings(), catalog(), Command())
    dialog.drafter.setEditText("draft.gguf")
    dialog.k_type.setCurrentIndex(dialog.k_type.findData("bf16"))
    values = dialog.values()
    assert values["--cache-type-k-draft"] == ["bf16"]
    assert "--cache-type-v-draft" not in values


def test_custom_mtp_q8_0_v_emits_draft_v_option():
    dialog = CustomMtpPresetDialog(AppSettings(), catalog(), Command())
    dialog.drafter.setEditText("draft.gguf")
    dialog.v_type.setCurrentIndex(dialog.v_type.findData("q8_0"))
    values = dialog.values()
    assert values["--cache-type-v-draft"] == ["q8_0"]
    assert "--cache-type-k-draft" not in values


def test_custom_mtp_combo_data_is_truthy_for_every_quant_choice():
    dialog = CustomMtpPresetDialog(AppSettings(), catalog(), Command())
    for combo in (dialog.k_type, dialog.v_type):
        assert combo.itemData(0) == ""
        for index in range(1, combo.count()):
            assert combo.itemData(index) == combo.itemText(index) != ""


def test_custom_mtp_prefills_existing_values_under_any_alias_spelling():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"], "model"),
        CommandArgument("-md", ["draft.gguf"], "draft_model"),
        CommandArgument("--spec-draft-type-k", ["bf16"]),
        CommandArgument("-ctvd", ["q8_0"]),
    ])
    dialog = CustomMtpPresetDialog(AppSettings(), catalog(), command)
    assert dialog.drafter.currentText() == "draft.gguf"
    assert dialog.k_type.currentData() == "bf16"
    assert dialog.v_type.currentData() == "q8_0"


def test_custom_mtp_prefill_retains_quant_outside_catalog_choices():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"], "model"),
        CommandArgument("--spec-draft-type-k", ["q3_k_s"]),
    ])
    dialog = CustomMtpPresetDialog(AppSettings(), catalog(), command)
    dialog.drafter.setEditText("d.gguf")
    assert dialog.k_type.currentData() == "q3_k_s"
    assert dialog.values()["--cache-type-k-draft"] == ["q3_k_s"]


def test_context_kv_prefills_via_catalog_aliases():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"], "model"),
        CommandArgument("-c", ["4096"]),
        CommandArgument("-ctk", ["q8_0"]),
        CommandArgument("--cache-type-v", ["q5_0"]),
        CommandArgument("-ctkd", ["bf16"]),
    ])
    dialog = ContextKvPresetDialog(catalog(), command)
    assert dialog.context.text() == "4096"
    assert dialog.k_type.currentText() == "q8_0"
    assert dialog.v_type.currentText() == "q5_0"
    assert dialog.draft.isChecked()


def test_device_split_prefills_and_detects_cpu_moe():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"], "model"),
        CommandArgument("-dev", ["Vulkan0,CUDA0"]),
        CommandArgument("-sm", ["tensor"]),
        CommandArgument("-ts", ["2,1"]),
        CommandArgument("-mg", ["1"]),
        CommandArgument("-ngl", ["all"]),
        CommandArgument("-cmoe"),
    ])
    dialog = DeviceSplitPresetDialog(catalog(), command)
    assert dialog.devices.text() == "Vulkan0,CUDA0"
    assert dialog.mode.currentText() == "tensor"
    assert dialog.tensor_split.text() == "2,1"
    assert dialog.main_gpu.text() == "1"
    assert dialog.gpu_layers.text() == "all"
    assert dialog.all_moe_cpu.isChecked()
