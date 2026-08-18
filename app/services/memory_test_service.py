from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from app.models.command import Command
from app.models.memory import MemoryTestResult
from app.services.flag_catalog import FlagCatalog
from app.services.memory_parser import MemoryBreakdownParser, parse_fitted_arguments, parse_supported_flags


_FIT_PRINT = ("--fit-print", "-fitp")
_FIT_TARGET = ("--fit-target", "-fitt")
_FIT_CONTEXT = ("--fit-ctx", "-fitc")


@dataclass(frozen=True)
class FitArgumentTranslation:
    argv: tuple[str, ...]
    skipped_arguments: tuple[str, ...]


def translate_fit_params_arguments(
    command: Command,
    catalog: FlagCatalog,
    supported_flags: frozenset[str],
    *,
    fit_target: str = "",
    fit_context: str = "",
) -> FitArgumentTranslation:
    """Translate structured server arguments and explicitly record unsupported inputs."""
    result: list[str] = []
    skipped: list[str] = []
    skipped_controls = set(_FIT_PRINT)
    overridden = (set(_FIT_TARGET) if fit_target else set()) | (set(_FIT_CONTEXT) if fit_context else set())
    for argument in command.arguments:
        if argument.source_type == "model_default_template":
            skipped.append(f"{argument.flag}: model-default template has no argv value")
            continue
        if argument.flag in skipped_controls:
            skipped.append(f"{argument.flag}: controlled by the memory test")
            continue
        if argument.flag in overridden:
            skipped.append(f"{argument.flag}: overridden by Memory Test Options")
            continue
        spec = catalog.find(argument.flag)
        aliases = spec.aliases if spec else (argument.flag,)
        selected = next((alias for alias in (argument.flag, *aliases) if alias in supported_flags), None)
        if selected is None:
            skipped.append(f"{argument.flag}: unsupported by this llama-fit-params build")
            continue
        result.append(selected)
        result.extend(argument.values)
    if fit_target:
        flag = _supported_alias(_FIT_TARGET, supported_flags)
        if flag:
            result.extend((flag, fit_target))
    if fit_context:
        flag = _supported_alias(_FIT_CONTEXT, supported_flags)
        if flag:
            result.extend((flag, fit_context))
    return FitArgumentTranslation(tuple(result), tuple(skipped))


def build_fit_params_argv(
    command: Command,
    catalog: FlagCatalog,
    supported_flags: frozenset[str],
    *,
    fit_target: str = "",
    fit_context: str = "",
) -> tuple[str, ...]:
    return translate_fit_params_arguments(command, catalog, supported_flags, fit_target=fit_target, fit_context=fit_context).argv


def _supported_alias(aliases: tuple[str, ...], supported: frozenset[str]) -> str | None:
    return next((alias for alias in aliases if alias in supported), None)


class MemoryTestService(QObject):
    """Asynchronous two-pass llama-fit-params execution: estimate, then fitted argv."""

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
        self._stage = ""
        self._binary = ""
        self._catalog: FlagCatalog | None = None
        self._command: Command | None = None
        self._base_argv: tuple[str, ...] = ()
        self._help_stdout = ""
        self._help_stderr = ""
        self._skipped_arguments: tuple[str, ...] = ()
        self._sidecars: tuple[tuple[str, str], ...] = ()
        self._fit_target = ""
        self._fit_context = ""
        self._estimate_stdout = ""
        self._estimate_stderr = ""
        self._fit_stdout = ""
        self._fit_stderr = ""
        self._estimate_code: int | None = None
        self._fit_code: int | None = None
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, command: Command, binary: str, catalog: FlagCatalog, *, fit_target: str = "", fit_context: str = "") -> None:
        if self.running:
            raise RuntimeError("A memory test is already running.")
        if not binary or not Path(binary).is_file():
            raise RuntimeError("llama-fit-params was not found. Select a llama.cpp Folder or choose an active fit binary in Settings.")
        self._binary, self._catalog, self._command = binary, catalog, command.copy()
        self._sidecars = self._find_sidecars(self._command, catalog)
        self._skipped_arguments = ()
        self._base_argv = ()
        self._help_stdout = self._help_stderr = ""
        self._estimate_stdout = self._estimate_stderr = self._fit_stdout = self._fit_stderr = ""
        self._estimate_code = self._fit_code = None
        self._fit_target, self._fit_context = fit_target, fit_context
        self._cancelled = False
        supported = self._supported_cache.get(binary)
        if supported is None:
            self._start_process("help", ("--help",))
            return
        self._start_estimate(supported, fit_target, fit_context)

    def cancel(self) -> None:
        if not self.running:
            return
        self._cancelled = True
        self.state_changed.emit("Cancelling memory test…")
        self.process.terminate()
        QTimer.singleShot(2_000, self._kill_if_running)
    def _kill_if_running(self) -> None:
        if self.running:
            self.process.kill()

    def _start_estimate(self, supported: frozenset[str], fit_target: str, fit_context: str) -> None:
        assert self._command is not None and self._catalog is not None
        translation = translate_fit_params_arguments(self._command, self._catalog, supported, fit_target=fit_target, fit_context=fit_context)
        self._base_argv = translation.argv
        self._skipped_arguments = translation.skipped_arguments
        if "-m" not in self._base_argv and "--model" not in self._base_argv:
            self._complete_error("Select a model before running a memory test.")
            return
        print_flag = _supported_alias(_FIT_PRINT, supported)
        if print_flag is None:
            self._complete_error("This llama-fit-params build does not expose --fit-print, so it cannot provide a memory breakdown.")
            return
        self._start_process("estimate", (*self._base_argv, print_flag, "on"))

    def _start_process(self, stage: str, arguments: tuple[str, ...]) -> None:
        self._stage = stage
        message = "Reading llama-fit-params capabilities…" if stage == "help" else ("Analyzing memory…" if stage == "estimate" else "Calculating fitted parameters…")
        self.state_changed.emit(message)
        self.process.setProgram(self._binary)
        self.process.setArguments(list(arguments))
        self.process.start()

    def _read_stdout(self) -> None:
        text = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        self._append_output(text, stderr=False)

    def _read_stderr(self) -> None:
        text = bytes(self.process.readAllStandardError()).decode(errors="replace")
        self._append_output(text, stderr=True)

    def _append_output(self, text: str, *, stderr: bool) -> None:
        target = f"_{self._stage}_{'stderr' if stderr else 'stdout'}"
        if hasattr(self, target):
            setattr(self, target, getattr(self, target) + text)

    def _finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        if not self._stage:
            return
        self._read_stdout()
        self._read_stderr()
        if self._cancelled:
            self._complete_error("Memory test cancelled.", exit_code=exit_code)
            return
        if self._stage == "help":
            help_output = self._help_stdout + self._help_stderr
            if exit_code != 0:
                self._complete_error("llama-fit-params --help failed.", exit_code=exit_code)
                return
            supported = parse_supported_flags(help_output)
            self._supported_cache[self._binary] = supported
            self._start_estimate(supported, self._fit_target, self._fit_context)
            return
        if self._stage == "estimate":
            self._estimate_code = exit_code
            combined = self._estimate_stdout + "\n" + self._estimate_stderr
            breakdown = MemoryBreakdownParser.parse(combined) if exit_code == 0 else None
            if exit_code != 0 or breakdown is None:
                reason = "llama-fit-params failed while estimating memory." if exit_code != 0 else "llama-fit-params returned no parseable memory breakdown."
                self._complete_error(reason, exit_code=exit_code, breakdown=breakdown)
                return
            self._start_process("fit", self._base_argv)
            return
        self._fit_code = exit_code
        breakdown = MemoryBreakdownParser.parse(self._estimate_stdout + "\n" + self._estimate_stderr)
        fitted = parse_fitted_arguments(self._fit_stdout + "\n" + self._fit_stderr) if exit_code == 0 else ()
        fit_error = "" if exit_code == 0 else "Automatic fitted parameters are unavailable for this configuration."
        self._complete(MemoryTestResult(
            breakdown=breakdown,
            fitted_arguments=fitted,
            skipped_arguments=self._skipped_arguments,
            sidecars=self._sidecars,
            raw_stdout=self._estimate_stdout + "\n" + self._fit_stdout,
            raw_stderr=self._estimate_stderr + "\n" + self._fit_stderr,
            exit_code=self._estimate_code,
            fit_exit_code=self._fit_code,
            fit_error=fit_error,
            requested_argv=self._base_argv,
        ))

    def _error(self, _error: QProcess.ProcessError) -> None:
        if self._stage and self.process.state() == QProcess.ProcessState.NotRunning and not self._cancelled:
            self._complete_error(f"Memory test process error: {self.process.errorString()}")

    def _complete_error(self, error: str, *, exit_code: int | None = None, breakdown=None) -> None:
        raw_stdout = self._estimate_stdout + "\n" + self._fit_stdout
        raw_stderr = self._estimate_stderr + "\n" + self._fit_stderr
        if self._stage == "help":
            raw_stdout, raw_stderr = self._help_stdout, self._help_stderr
        self._complete(MemoryTestResult(
            breakdown=breakdown,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
            exit_code=self._estimate_code if self._estimate_code is not None else exit_code,
            fit_exit_code=self._fit_code,
            error=error,
            requested_argv=self._base_argv,
            skipped_arguments=self._skipped_arguments,
            sidecars=self._sidecars,
        ))

    @staticmethod
    def _find_sidecars(command: Command, catalog: FlagCatalog) -> tuple[tuple[str, str], ...]:
        labels = {"--mmproj": "MMProj", "--spec-draft-model": "Draft model", "--model-draft": "Draft model"}
        return tuple(
            (labels[spec.canonical_name], argument.values[0])
            for argument in command.arguments
            if (spec := catalog.find(argument.flag)) is not None
            and spec.canonical_name in labels
            and argument.values
            and argument.values[0]
        )

    def _complete(self, result: MemoryTestResult) -> None:
        self._stage = ""
        self.state_changed.emit("Memory test complete" if result.success else "Memory test failed")
        self.completed.emit(result)
