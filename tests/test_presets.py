import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.models.command import CommandArgument
from app.models.flags import FlagSpec
from app.services.flag_catalog import FlagCatalog
from app.settings import AppSettings
from app.widgets.searchable_flag_picker import SearchableFlagPicker
from app.widgets.command_builder import CommandBuilder


def builder():
    app = QApplication.instance() or QApplication([])
    specs = [
        FlagSpec("--model", ("-m", "--model"), "model", 1),
        FlagSpec("--ctx-size", ("-c", "--ctx-size"), "context", 1),
        FlagSpec("--cache-type-k", ("-ctk", "--cache-type-k"), "K", 1),
        FlagSpec("--cache-type-v", ("-ctv", "--cache-type-v"), "V", 1),
        FlagSpec("--cache-type-k-draft", ("--cache-type-k-draft",), "draft K", 1),
        FlagSpec("--cache-type-v-draft", ("--cache-type-v-draft",), "draft V", 1),
        FlagSpec("--device", ("-dev", "--device"), "devices", 1),
        FlagSpec("--tensor-split", ("-ts", "--tensor-split"), "split", 1),
        FlagSpec("--cpu-moe", ("-cmoe", "--cpu-moe"), "moe", 0),
        FlagSpec("--n-cpu-moe", ("-ncmoe", "--n-cpu-moe"), "moe layers", 1),
        FlagSpec("--spec-type", ("--spec-type",), "spec", 1),
        FlagSpec("--spec-draft-model", ("-md", "--spec-draft-model"), "draft", 1),
    ]
    return CommandBuilder(AppSettings(), FlagCatalog(specs))


def test_set_argument_updates_alias_without_duplicate():
    widget = builder()
    widget.command.arguments.append(CommandArgument("-c", ["4096"]))
    widget.set_argument("--ctx-size", ["131072"])
    contexts = [argument for argument in widget.command.arguments if argument.flag in {"-c", "--ctx-size"}]
    assert contexts == [CommandArgument("-c", ["131072"], "preset")]


def test_device_split_values_remain_single_argv_tokens():
    widget = builder()
    widget.set_argument("--device", ["Vulkan0,CUDA0"])
    widget.set_argument("--tensor-split", ["2,1"])
    argv = widget.command.argv()
    assert argv[argv.index("-dev") + 1] == "Vulkan0,CUDA0"
    assert argv[argv.index("-ts") + 1] == "2,1"


def test_custom_mtp_updates_existing_logical_arguments_once():
    widget = builder()
    for name, values in {"--spec-draft-model": ["draft.gguf"], "--spec-type": ["draft-mtp"]}.items():
        widget.set_argument(name, values)
        widget.set_argument(name, values)
    assert len([argument for argument in widget.command.arguments if argument.flag == "-md"]) == 1
    assert len([argument for argument in widget.command.arguments if argument.flag == "--spec-type"]) == 1



def test_picker_defaults_to_common_and_advanced_reveals_catalog():
    app = QApplication.instance() or QApplication([])
    catalog = FlagCatalog([
        FlagSpec("--ctx-size", ("-c", "--ctx-size"), "context", 1),
        FlagSpec("--uncommon-new-flag", ("--uncommon-new-flag",), "advanced", 1),
    ])
    picker = SearchableFlagPicker(catalog)
    default_flags = {picker.results.item(index).data(256 + 1) for index in range(picker.results.count())}
    assert "--uncommon-new-flag" not in default_flags
    picker.advanced.setChecked(True)
    advanced_flags = {picker.results.item(index).data(256 + 1) for index in range(picker.results.count())}
    assert "--uncommon-new-flag" in advanced_flags

def test_builder_normalizes_imported_executable_to_configured_server():
    settings = AppSettings(last_command={"executable": r"D:\Engines\other\llama-server.exe", "arguments": [{"flag": "-m", "values": ["model.gguf"]}]})
    widget = CommandBuilder(settings, FlagCatalog([FlagSpec("--model", ("-m", "--model"), "model", 1)]))
    assert widget.command.rendered_lines().splitlines()[0] == "Engines/llama.cpp/build-mixed/bin/Release/llama-server.exe"