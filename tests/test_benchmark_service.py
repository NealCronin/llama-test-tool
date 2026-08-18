import json

import pytest

from app.models.benchmark import BenchmarkOptions
from app.models.command import Command, CommandArgument
from app.models.flags import FlagSpec
from app.services.benchmark_service import parse_benchmark_json, translate_command
from app.services.flag_catalog import FlagCatalog


SUPPORTED = frozenset({
    "-m", "-b", "-ub", "-ctk", "-ctv", "-t", "-C", "--cpu-strict", "--poll",
    "-ngl", "-ncmoe", "-sm", "-mg", "-fa", "-dev", "-ts", "-ot", "-nkvo",
    "-nopo", "--no-host", "-fitt", "-fitc", "-p", "-n", "-r", "-d", "-o",
})


def catalog() -> FlagCatalog:
    names = (
        ("--model", ("-m", "--model")), ("--batch-size", ("-b", "--batch-size")),
        ("--ubatch-size", ("-ub", "--ubatch-size")), ("--cache-type-k", ("-ctk", "--cache-type-k")),
        ("--cache-type-v", ("-ctv", "--cache-type-v")), ("--threads", ("-t", "--threads")),
        ("--cpu-mask", ("-C", "--cpu-mask")), ("--cpu-strict", ("--cpu-strict",)),
        ("--poll", ("--poll",)), ("--gpu-layers", ("-ngl", "--gpu-layers")),
        ("--n-cpu-moe", ("-ncmoe", "--n-cpu-moe")), ("--split-mode", ("-sm", "--split-mode")),
        ("--main-gpu", ("-mg", "--main-gpu")), ("--flash-attn", ("-fa", "--flash-attn")),
        ("--device", ("-dev", "--device")), ("--tensor-split", ("-ts", "--tensor-split")),
        ("--override-tensor", ("-ot", "--override-tensor")), ("--no-kv-offload", ("-nkvo", "--no-kv-offload")),
        ("--no-op-offload", ("--no-op-offload",)), ("--no-host", ("--no-host",)),
        ("--port", ("--port",)), ("--jinja", ("--jinja",)),
    )
    return FlagCatalog([FlagSpec(name, aliases, name, 1 if name not in {"--no-kv-offload", "--no-op-offload", "--no-host", "--jinja"} else 0) for name, aliases in names])


def test_translates_compatible_multigpu_command_without_mutation():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"]), CommandArgument("-ngl", ["all"]),
        CommandArgument("-sm", ["layer"]), CommandArgument("-ts", ["2,1"]),
        CommandArgument("-dev", ["Vulkan0,CUDA0"]), CommandArgument("-fa", ["on"]),
        CommandArgument("-ctk", ["q8_0"]), CommandArgument("-ctv", ["q8_0"]),
        CommandArgument("-b", ["2048"]), CommandArgument("-ub", ["512"]),
        CommandArgument("-ncmoe", ["4"]), CommandArgument("--port", ["8080"]), CommandArgument("--jinja"),
    ])
    original = command.to_dict()
    translated = translate_command(command, catalog(), SUPPORTED, BenchmarkOptions())
    assert translated.argv[:2] == ("-m", "model.gguf")
    assert ("-ngl", "-1") == translated.argv[2:4]
    assert "2/1" in translated.argv and "Vulkan0/CUDA0" in translated.argv
    assert "-dev Vulkan0,CUDA0 -> -dev Vulkan0/CUDA0" in translated.translated_arguments
    assert "--port: server-only or unsupported by llama-bench" in translated.skipped_arguments
    assert "--jinja: server-only or unsupported by llama-bench" in translated.skipped_arguments
    assert "-ngl all -> -ngl -1" in translated.translated_arguments
    assert command.to_dict() == original


def test_preserves_integer_gpu_layers_and_converts_boolean_polarity():
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"]), CommandArgument("-ngl", ["48"]),
        CommandArgument("--no-kv-offload"), CommandArgument("--no-op-offload"), CommandArgument("--no-host"),
    ])
    translated = translate_command(command, catalog(), SUPPORTED, BenchmarkOptions())
    assert ("-ngl", "48") == translated.argv[2:4]
    assert ("-nkvo", "1") == translated.argv[4:6]
    assert ("-nopo", "1") == translated.argv[6:8]
    assert ("--no-host", "1") == translated.argv[8:10]


def test_parse_benchmark_json_multiple_tests_and_preserves_unknown_fields():
    output = json.dumps([
        {"model_filename": "model.gguf", "backends": "Vulkan, CUDA", "gpu_info": "GPU", "n_prompt": 512, "n_gen": 0, "n_depth": 0, "avg_ts": 1428.6, "stddev_ts": 12.3, "n_batch": 2048, "n_ubatch": 512, "n_threads": 8, "n_gpu_layers": -1, "n_cpu_moe": 0, "split_mode": "layer", "tensor_split": "2,1", "type_k": "q8_0", "type_v": "q8_0", "build_commit": "abc"},
        {"model_filename": "model.gguf", "backends": "Vulkan, CUDA", "gpu_info": "GPU", "n_prompt": 0, "n_gen": 128, "n_depth": 0, "avg_ts": 37.8, "stddev_ts": 0.4, "n_batch": 2048, "n_ubatch": 512, "n_threads": 8, "n_gpu_layers": -1, "n_cpu_moe": 0, "split_mode": "layer", "tensor_split": "2,1", "type_k": "q8_0", "type_v": "q8_0"},
    ])
    tests = parse_benchmark_json(output)
    assert [(test.test_type, test.tokens_per_second) for test in tests] == [("pp", 1428.6), ("tg", 37.8)]
    assert tests[0].extra["build_commit"] == "abc"


def test_parse_benchmark_json_rejects_malformed_or_empty_results():
    with pytest.raises(ValueError): parse_benchmark_json("not json")
    with pytest.raises(ValueError): parse_benchmark_json("[]")
    with pytest.raises(ValueError): parse_benchmark_json('[{"n_prompt": 1}]')
