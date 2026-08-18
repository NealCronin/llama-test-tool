from app.models.command import Command, CommandArgument
from app.models.memory import MemoryBreakdown
from app.services.flag_catalog import FlagCatalog
from app.models.memory import MemoryTestResult
from app.services.memory_parser import MemoryBreakdownParser, parse_fitted_arguments, parse_supported_flags
from app.services.memory_test_service import build_fit_params_argv, translate_fit_params_arguments


HEADER = "prefix: | memory breakdown [MiB] | total free self model context compute unaccounted |"


def test_translates_current_command_using_installed_supported_aliases():
    catalog = FlagCatalog(FlagCatalog.fallback_specs())
    # Add options not present in the small fallback catalog to exercise exact-flag retention.
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"]), CommandArgument("-c", ["131072"]),
        CommandArgument("-ctk", ["q8_0"]), CommandArgument("-ctv", ["q8_0"]),
        CommandArgument("-ngl", ["all"]), CommandArgument("--port", ["${PORT}"]),
        CommandArgument("--host", ["127.0.0.1"]),
    ])
    supported = frozenset({"-m", "-c", "-ctk", "-ctv", "-ngl", "--fit-print"})
    argv = build_fit_params_argv(command, catalog, supported)
    assert argv == ("-m", "model.gguf", "-c", "131072", "-ctk", "q8_0", "-ctv", "q8_0", "-ngl", "all")
    assert "--port" not in argv and "--host" not in argv


def test_translation_reports_arguments_skipped_by_the_installed_binary():
    catalog = FlagCatalog(FlagCatalog.fallback_specs())
    command = Command(arguments=[
        CommandArgument("-m", ["model.gguf"]),
        CommandArgument("--port", ["8080"]),
        CommandArgument("-fa", ["on"]),
    ])
    translation = translate_fit_params_arguments(command, catalog, frozenset({"-m"}))
    assert translation.argv == ("-m", "model.gguf")
    assert translation.skipped_arguments == (
        "--port: unsupported by this llama-fit-params build",
        "-fa: unsupported by this llama-fit-params build",
    )


def test_fit_target_override_preserves_current_fit_context():
    command = Command(arguments=[CommandArgument("-m", ["model.gguf"]), CommandArgument("-fitt", ["512"]), CommandArgument("-fitc", ["8192"])])
    translation = translate_fit_params_arguments(command, FlagCatalog(FlagCatalog.fallback_specs()), frozenset({"-m", "-fitt", "-fitc"}), fit_target="1024")
    assert translation.argv == ("-m", "model.gguf", "-fitc", "8192", "-fitt", "1024")


def test_fit_context_override_preserves_current_fit_target():
    command = Command(arguments=[CommandArgument("-m", ["model.gguf"]), CommandArgument("-fitt", ["512"]), CommandArgument("-fitc", ["8192"])])
    translation = translate_fit_params_arguments(command, FlagCatalog(FlagCatalog.fallback_specs()), frozenset({"-m", "-fitt", "-fitc"}), fit_context="4096")
    assert translation.argv == ("-m", "model.gguf", "-fitt", "512", "-fitc", "4096")


def test_parser_handles_prefixed_single_device_and_host_rows():
    output = "\n".join((
        HEADER,
        "log: | - CUDA0 (RTX 4090) | 24077 = 945 + (19187 = 17904 + 384 + 898) + 3945 |",
        "log: | - Host | 58271 = 58259 + 0 + 12 |",
    ))
    breakdown = MemoryBreakdownParser.parse(output)
    assert breakdown is not None
    assert [device.device_name for device in breakdown.devices] == ["CUDA0 (RTX 4090)", "Host"]
    assert breakdown.total_model_mib == 76163
    assert breakdown.total_context_mib == 384
    assert breakdown.total_compute_mib == 910

def test_parser_handles_current_compact_fit_params_output():
    breakdown = MemoryBreakdownParser.parse("CUDA0 261 23 64\nVulkan2 856 43 563\nHost 485 0 20\n")
    assert breakdown is not None
    assert [device.device_name for device in breakdown.devices] == ["CUDA0", "Vulkan2", "Host"]
    assert breakdown.total_model_mib == 1602
    assert breakdown.total_context_mib == 66
    assert breakdown.total_compute_mib == 647


def test_parser_handles_mixed_devices_whitespace_and_aggregates():
    output = "\n".join((
        "  | MEMORY BREAKDOWN [MiB] | total free self model context compute unaccounted |",
        "x | - Vulkan0 (AMD Radeon) | 16000 = 1000 + (12000 = 10000 + 1500 + 500) + 3000 |",
        "x | - CUDA1 | 24000 = 2000 + (18000 = 15000 + 2000 + 1000) + 4000 |",
        "x | - Host | 512 = 400 + 50 + 62 |",
    ))
    breakdown = MemoryBreakdownParser.parse(output)
    assert breakdown is not None
    assert breakdown.total_model_mib == 25400
    assert breakdown.total_context_mib == 3550
    assert breakdown.total_compute_mib == 1562
    assert breakdown.total_self_mib == 30512


def test_parser_rejects_malformed_output_and_fitted_parser_is_conservative():
    assert MemoryBreakdownParser.parse("CUDA0 model = 1") is None
    assert parse_fitted_arguments("Printing fitted\n-c 4096 -ngl 48 -ts 2,1") == ("-c", "4096", "-ngl", "48", "-ts", "2,1")
    assert parse_fitted_arguments("-c 4096 | bad") == ()

def test_failed_result_never_reports_success_with_missing_breakdown():
    result = MemoryTestResult(breakdown=None, exit_code=0, fit_exit_code=0)
    assert not result.success


def test_supported_flags_come_from_help_text():
    flags = parse_supported_flags("-c, --ctx-size N\n-fitp, --fit-print [on|off]\n")
    assert {"-c", "--ctx-size", "-fitp", "--fit-print"} <= flags


def test_fit_status_distinguishes_explicit_changes_from_unknown_defaults():
    changed = MemoryTestResult(breakdown=MemoryBreakdown(()), fitted_arguments=("-c", "65536"), requested_argv=("-m", "model.gguf"))
    returned = MemoryTestResult(breakdown=MemoryBreakdown(()), fitted_arguments=("-c", "65536"), requested_argv=("-m", "model.gguf", "-c", "65536"))
    unchanged = MemoryTestResult(breakdown=MemoryBreakdown(()), fitted_arguments=("-c", "65536"), requested_argv=("-m", "model.gguf", "-c", "65536"), raw_stdout="no changes needed")
    assert changed.fit_status == "fitted"
    assert returned.fit_status == "returned"
    assert unchanged.fit_status == "unchanged"


def test_estimate_remains_successful_when_fitting_fails():
    result = MemoryTestResult(breakdown=MemoryBreakdown(()), exit_code=0, fit_exit_code=1, fit_error="Automatic fitting unavailable")
    assert result.estimate_success
    assert not result.fit_success
    assert result.success
