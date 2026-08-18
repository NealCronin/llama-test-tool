from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from app.models.benchmark import BenchmarkArgumentTranslation, BenchmarkOptions, BenchmarkResult, BenchmarkTest
from app.models.command import Command
from app.services.flag_catalog import FlagCatalog
from app.services.memory_parser import parse_supported_flags


# A server argument is eligible only when llama-bench advertises one of these
# aliases. The mapping is deliberately semantic, rather than a blind argv copy.
_PASSTHROUGH: dict[str, tuple[str, ...]] = {
    "--model": ("-m", "--model"),
    "--batch-size": ("-b", "--batch-size"),
    "--ubatch-size": ("-ub", "--ubatch-size"),
    "--cache-type-k": ("-ctk", "--cache-type-k"),
    "--cache-type-v": ("-ctv", "--cache-type-v"),
    "--threads": ("-t", "--threads"),
    "--cpu-mask": ("-C", "--cpu-mask"),
    "--cpu-strict": ("--cpu-strict",),
    "--poll": ("--poll",),
    "--n-cpu-moe": ("-ncmoe", "--n-cpu-moe"),
    "--split-mode": ("-sm", "--split-mode"),
    "--main-gpu": ("-mg", "--main-gpu"),
    "--flash-attn": ("-fa", "--flash-attn"),
    "--load-mode": ("--load-mode",),
    "--fit-target": ("-fitt", "--fit-target"),
    "--fit-ctx": ("-fitc", "--fit-ctx"),
}


def _supported_alias(aliases: tuple[str, ...], supported: frozenset[str]) -> str | None:
    return next((alias for alias in aliases if alias in supported), None)


def translate_command(command: Command, catalog: FlagCatalog, supported_flags: frozenset[str], options: BenchmarkOptions) -> BenchmarkArgumentTranslation:
    """Translate a server configuration to independently parsed llama-bench argv."""
    argv: list[str] = []
    skipped: list[str] = []
    translated: list[str] = []
    warnings: list[str] = []

    def append(targets: tuple[str, ...], values: list[str], source: str) -> bool:
        target = _supported_alias(targets, supported_flags)
        if target is None:
            skipped.append(f"{source}: unsupported by this llama-bench build")
            return False
        argv.append(target)
        argv.extend(values)
        return True

    for argument in command.arguments:
        if argument.source_type == "model_default_template":
            skipped.append(f"{argument.flag}: model-default template has no argv value")
            continue
        spec = catalog.find(argument.flag)
        canonical = spec.canonical_name if spec else argument.flag
        values = list(argument.values)
        if canonical in {"--device", "--tensor-split", "--override-tensor"}:
            value = values[0] if values else ""
            source_separator, bench_separator = {
                "--device": (",", "/"), "--tensor-split": (",", "/"), "--override-tensor": (",", ";"),
            }[canonical]
            translated_value = value.replace(source_separator, bench_separator)
            targets = {
                "--device": ("-dev", "--device"),
                "--tensor-split": ("-ts", "--tensor-split"),
                "--override-tensor": ("-ot", "--override-tensor"),
            }[canonical]
            if append(targets, [translated_value], argument.flag) and translated_value != value:
                translated.append(f"{argument.flag} {value} -> {targets[0]} {translated_value}")
            continue
        if canonical == "--gpu-layers":
            value = values[0] if values else ""
            if value in {"all", "auto"}:
                if append(("-ngl", "--n-gpu-layers"), ["-1"], argument.flag):
                    translated.append(f"{argument.flag} {value} -> -ngl -1" + (" (approximation)" if value == "auto" else ""))
                    if value == "auto":
                        warnings.append("Server -ngl auto uses automatic placement. llama-bench has no equivalent here; this benchmark approximates it with explicit -ngl -1.")
            else:
                try:
                    int(value)
                except ValueError:
                    skipped.append(f"{argument.flag} {value!r}: llama-bench requires a signed integer")
                else:
                    append(("-ngl", "--n-gpu-layers"), [value], argument.flag)
            continue
        if canonical in _PASSTHROUGH:
            if not values and canonical != "--model":
                skipped.append(f"{argument.flag}: no explicit value to benchmark")
            else:
                append(_PASSTHROUGH[canonical], values, argument.flag)
            continue
        if canonical in {"--kv-offload", "--no-kv-offload"}:
            value = "1" if canonical == "--no-kv-offload" or spec and spec.is_negative(argument.flag) else "0"
            if append(("-nkvo", "--no-kv-offload"), [value], argument.flag):
                translated.append(f"{argument.flag} -> -nkvo {value}")
            continue
        if canonical in {"--op-offload", "--no-op-offload"}:
            value = "1" if canonical == "--no-op-offload" or spec and spec.is_negative(argument.flag) else "0"
            if append(("-nopo", "--no-op-offload"), [value], argument.flag):
                translated.append(f"{argument.flag} -> -nopo {value}")
            continue
        if canonical == "--no-host":
            if append(("--no-host",), ["1"], argument.flag):
                translated.append(f"{argument.flag} -> --no-host 1")
            continue
        if canonical in {"--mmap", "--no-mmap"}:
            value = "0" if canonical == "--no-mmap" or spec and spec.is_negative(argument.flag) else "1"
            if append(("-mmp", "--mmap"), [value], argument.flag):
                translated.append(f"{argument.flag} -> -mmp {value}")
            continue
        skipped.append(f"{argument.flag}: server-only or unsupported by llama-bench")

    workload = (("-p", "--n-prompt"), str(options.prompt_tokens), ("-n", "--n-gen"), str(options.generation_tokens), ("-r", "--repetitions"), str(options.repetitions), ("-d", "--n-depth"), str(options.context_depth))
    for aliases, value in zip(workload[::2], workload[1::2]):
        target = _supported_alias(aliases, supported_flags)
        if target is None:
            warnings.append(f"Installed llama-bench does not expose {'/'.join(aliases)}.")
        else:
            argv.extend((target, value))
    if options.delay:
        append(("--delay",), [str(options.delay)], "--delay")
    if options.no_warmup:
        append(("--no-warmup",), [], "--no-warmup")
    output = _supported_alias(("-o", "--output"), supported_flags)
    if output is None:
        warnings.append("Installed llama-bench does not expose JSON output (-o/--output).")
    else:
        argv.extend((output, "json"))
    return BenchmarkArgumentTranslation(tuple(argv), tuple(skipped), tuple(translated), tuple(warnings))


def parse_benchmark_json(text: str) -> tuple[BenchmarkTest, ...]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"llama-bench returned malformed JSON: {error.msg}") from error
    if not isinstance(data, list) or not data:
        raise ValueError("llama-bench JSON must be a non-empty result array.")
    tests: list[BenchmarkTest] = []
    for item in data:
        if not isinstance(item, dict) or "avg_ts" not in item:
            raise ValueError("llama-bench JSON contains an invalid result record.")
        remaining: dict[str, Any] = dict(item)

        def get(name: str, default: Any = "") -> Any:
            return remaining.pop(name, default)

        prompt, generated = int(get("n_prompt", 0)), int(get("n_gen", 0))
        tests.append(BenchmarkTest(
            model=str(get("model_filename")), backend=str(get("backends")), gpu_info=str(get("gpu_info")),
            test_type="pg" if prompt and generated else "pp" if prompt else "tg" if generated else "unknown",
            prompt_tokens=prompt, generation_tokens=generated, depth=int(get("n_depth", 0)),
            tokens_per_second=float(get("avg_ts")), stddev_tokens_per_second=float(get("stddev_ts", 0)),
            batch=int(get("n_batch", 0)), ubatch=int(get("n_ubatch", 0)), threads=int(get("n_threads", 0)),
            gpu_layers=int(get("n_gpu_layers", 0)), cpu_moe_layers=int(get("n_cpu_moe", 0)),
            split_mode=str(get("split_mode", "")), tensor_split=str(get("tensor_split", "")),
            cache_type_k=str(get("type_k", "")), cache_type_v=str(get("type_v", "")), extra=remaining,
        ))
    return tuple(tests)


class BenchmarkService(QObject):
    """Capability-aware, non-blocking llama-bench runner."""
    state_changed = Signal(str)
    completed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._supported_cache: dict[str, frozenset[str]] = {}
        self._binary = ""
        self._command: Command | None = None
        self._catalog: FlagCatalog | None = None
        self._options = BenchmarkOptions()
        self._translation = BenchmarkArgumentTranslation(())
        self._stage = ""
        self._help_stdout = self._help_stderr = self._stdout = self._stderr = ""
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def query_capabilities(self, binary: str) -> frozenset[str] | None:
        return self._supported_cache.get(binary)

    def start(self, command: Command, binary: str, catalog: FlagCatalog, options: BenchmarkOptions) -> None:
        if self.running:
            raise RuntimeError("A benchmark is already running.")
        if not binary or not Path(binary).is_file():
            raise RuntimeError("llama-bench was not found. Select a llama.cpp Folder and choose a detected llama-bench executable in Settings.")
        if command.copy().model_path() is None:
            raise RuntimeError("Select a model before benchmarking.")
        self._binary, self._command, self._catalog, self._options = binary, command.copy(), catalog, options
        self._help_stdout = self._help_stderr = self._stdout = self._stderr = ""
        self._cancelled = False
        supported = self._supported_cache.get(binary)
        if supported is None:
            self._start_process("help", ("--help",))
        else:
            self._run_with_capabilities(supported)

    def cancel(self) -> None:
        if self.running:
            self._cancelled = True
            self.state_changed.emit("Cancelling benchmark…")
            self.process.terminate()
            QTimer.singleShot(2_000, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self.running:
            self.process.kill()

    def _run_with_capabilities(self, supported: frozenset[str]) -> None:
        assert self._command is not None and self._catalog is not None
        self._translation = translate_command(self._command, self._catalog, supported, self._options)
        if not any(flag in self._translation.argv for flag in ("-m", "--model")):
            self._complete_error("The installed llama-bench cannot accept the selected model argument.")
            return
        if not any(flag in self._translation.argv for flag in ("-o", "--output")):
            self._complete_error("This llama-bench build lacks JSON output, required for structured results.")
            return
        self._start_process("benchmark", self._translation.argv)

    def _start_process(self, stage: str, arguments: tuple[str, ...]) -> None:
        self._stage = stage
        self.state_changed.emit("Reading llama-bench capabilities…" if stage == "help" else "Running llama-bench inference benchmark…")
        self.process.setProgram(self._binary)
        self.process.setArguments(list(arguments))
        self.process.start()

    def _read_stdout(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if self._stage == "help": self._help_stdout += text
        else: self._stdout += text

    def _read_stderr(self) -> None:
        text = bytes(self.process.readAllStandardError()).decode(errors="replace")
        if self._stage == "help": self._help_stderr += text
        else: self._stderr += text

    def _finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        self._read_stdout(); self._read_stderr()
        if self._cancelled:
            self._complete_error("Benchmark cancelled.", exit_code)
        elif self._stage == "help":
            if exit_code:
                self._complete_error("llama-bench --help failed.", exit_code)
            else:
                supported = parse_supported_flags(self._help_stdout + self._help_stderr)
                self._supported_cache[self._binary] = supported
                self._run_with_capabilities(supported)
        elif exit_code:
            self._complete_error("llama-bench failed. See raw output for backend, device, or memory errors.", exit_code)
        else:
            try:
                tests = parse_benchmark_json(self._stdout)
            except ValueError as error:
                self._complete_error(str(error), exit_code)
            else:
                self._complete(BenchmarkResult(tests, self._translation.skipped_arguments, self._translation.translated_arguments, self._translation.warnings, self._translation.argv, self._stdout, self._stderr, exit_code))

    def _error(self, _error: QProcess.ProcessError) -> None:
        if self._stage and self.process.state() == QProcess.ProcessState.NotRunning and not self._cancelled:
            self._complete_error(f"Benchmark process error: {self.process.errorString()}")

    def _complete_error(self, error: str, exit_code: int | None = None) -> None:
        self._complete(BenchmarkResult(skipped_arguments=self._translation.skipped_arguments, translated_arguments=self._translation.translated_arguments, warnings=self._translation.warnings, sent_argv=self._translation.argv, raw_stdout=self._stdout or self._help_stdout, raw_stderr=self._stderr or self._help_stderr, exit_code=exit_code, error=error))

    def _complete(self, result: BenchmarkResult) -> None:
        self._stage = ""
        self.state_changed.emit("Benchmark complete" if result.success else "Benchmark failed")
        self.completed.emit(result)
