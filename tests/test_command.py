from app.models.command import Command, CommandArgument
from app.services.command_parser import parse_command
from app.services.flag_catalog import FlagCatalog
from app.services.validation import validate_command
from app.widgets.argument_row import ArgumentRow


def catalog() -> FlagCatalog:
    return FlagCatalog.fallback_specs() and FlagCatalog(FlagCatalog.fallback_specs())


def test_render_quotes_paths_and_retains_port_macro():
    command = Command(arguments=[
        CommandArgument("-m", [r"D:\Models\Qwen 3\model.gguf"], "model"),
        CommandArgument("-mm", [r"D:\MMProj\vision projector.gguf"], "mmproj"),
        CommandArgument("--port", ["${PORT}"]),
        CommandArgument("-fa", ["on"]),
    ])
    rendered = command.rendered("llama-server")
    assert '"D:\\Models\\Qwen 3\\model.gguf"' in rendered
    assert '"D:\\MMProj\\vision projector.gguf"' in rendered
    assert "${PORT}" in rendered
    assert command.argv("llama-server", port="53217")[6] == "53217"
    lines = command.rendered_lines("llama-server")
    assert lines.splitlines()[0] == "llama-server"
    assert "--port" in lines.splitlines()


def test_render_builtin_mtp_dflash_template_and_enum():
    command = Command(arguments=[
        CommandArgument("-m", ["main.gguf"]),
        CommandArgument("--spec-type", ["draft-mtp"]),
        CommandArgument("-md", ["draft.gguf"]),
        CommandArgument("--spec-type", ["draft-dflash"]),
        CommandArgument("--jinja"),
        CommandArgument("--chat-template-file", ["template.jinja"]),
        CommandArgument("-sm", ["layer"]),
    ])
    rendered = command.rendered("llama-server")
    for value in ("draft-mtp", "draft-dflash", "draft.gguf", "template.jinja", "layer"):
        assert value in rendered


def test_simple_command_round_trip_retains_semantics():
    original = Command(arguments=[
        CommandArgument("-m", [r"D:\Models\a model.gguf"]),
        CommandArgument("-fa", ["auto"]),
        CommandArgument("--ctx-size", ["131072"]),
    ])
    result = parse_command(original.rendered("llama-server"), catalog())
    assert result.raw_reason is None
    assert result.command.argv("llama-server") == original.argv("llama-server")


def test_shell_syntax_uses_raw_mode():
    result = parse_command("llama-server -m model.gguf | tee output.log", catalog())
    assert result.command is None
    assert "shell syntax" in result.raw_reason

def test_optional_value_flag_consumes_only_a_plain_following_value():
    result = parse_command("llama-server -m model.gguf -fa -c 4096", catalog())
    assert result.raw_reason is None
    assert result.command.arguments[1] == CommandArgument("-fa", [])
    assert result.command.arguments[2] == CommandArgument("-c", ["4096"])


def test_import_keeps_negative_flag_spelling():
    result = parse_command("llama-server -m model.gguf --no-jinja", catalog())
    assert result.raw_reason is None
    assert result.command.arguments[1].flag == "--no-jinja"

def test_gpu_layers_accepts_documented_symbolic_values():
    from app.models.flags import FlagSpec

    gpu = FlagSpec("--gpu-layers", ("-ngl", "--gpu-layers"), "GPU layers", 1, ("N",), choices=("auto", "all"), value_type="integer_or_choices")
    model = FlagSpec("--model", ("-m", "--model"), "model", 1, ("FILE",))
    catalog_with_gpu = FlagCatalog([model, gpu])
    command = Command(arguments=[CommandArgument("-m", ["missing.gguf"]), CommandArgument("-ngl", ["all"])])
    issues = validate_command(command, catalog_with_gpu)
    assert not any("integer" in issue.message for issue in issues)


def test_legacy_draft_source_metadata_is_removed_on_restore():
    argument = CommandArgument.from_dict({
        "flag": "-md", "values": [r"D:\Drafters\draft.gguf"], "source_type": "draft_model",
        "metadata": {"draft_source": r"D:\DFlash"},
    })
    assert argument.values == [r"D:\Drafters\draft.gguf"]
    assert argument.metadata == {}


def test_representative_structured_argv_preserves_each_value_as_one_token():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"]),
        CommandArgument("-dev", ["CUDA0,Vulkan1"]),
        CommandArgument("-ts", ["2,1"]),
        CommandArgument("-fitt", ["1024,2048"]),
        CommandArgument("-ot", ["blk.*=CPU"]),
        CommandArgument("-ngl", ["all"]),
    ])
    assert command.argv("llama-server") == [
        "llama-server", "-m", "model.gguf", "-dev", "CUDA0,Vulkan1",
        "-ts", "2,1", "-fitt", "1024,2048", "-ot", "blk.*=CPU", "-ngl", "all",
    ]
