from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeviceMemoryBreakdown:
    device_name: str
    model_mib: float
    context_mib: float
    compute_mib: float
    total_mib: float | None = None
    free_mib: float | None = None
    self_mib: float | None = None
    unaccounted_mib: float | None = None

    @property
    def categorized_mib(self) -> float:
        return self.model_mib + self.context_mib + self.compute_mib


@dataclass(frozen=True)
class MemoryBreakdown:
    devices: tuple[DeviceMemoryBreakdown, ...]

    @property
    def total_model_mib(self) -> float:
        return sum(device.model_mib for device in self.devices)

    @property
    def total_context_mib(self) -> float:
        return sum(device.context_mib for device in self.devices)

    @property
    def total_compute_mib(self) -> float:
        return sum(device.compute_mib for device in self.devices)

    @property
    def total_self_mib(self) -> float:
        return sum((device.self_mib if device.self_mib is not None else device.categorized_mib) for device in self.devices)


@dataclass(frozen=True)
class MemoryTestResult:
    breakdown: MemoryBreakdown | None
    fitted_arguments: tuple[str, ...] = ()
    raw_stdout: str = ""
    raw_stderr: str = ""
    exit_code: int | None = None
    fit_exit_code: int | None = None
    error: str = ""
    requested_argv: tuple[str, ...] = ()
    skipped_arguments: tuple[str, ...] = ()
    sidecars: tuple[tuple[str, str], ...] = ()

    @property
    def raw_output(self) -> str:
        return self.raw_stdout + ("\n" if self.raw_stdout and self.raw_stderr else "") + self.raw_stderr

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and self.fit_exit_code == 0 and self.breakdown is not None and not self.error

    @property
    def was_fitted(self) -> bool:
        return self.fit_status == "fitted"

    @property
    def fit_status(self) -> str:
        fitted = _normalized_values(self.fitted_arguments)
        if not fitted:
            return "unknown"
        requested = _normalized_values(self.requested_argv)
        if any(name not in requested or requested[name] != value for name, value in fitted.items()):
            return "fitted"
        if "no changes needed" in self.raw_output.casefold():
            return "unchanged"
        return "returned"


def _normalized_values(argv: tuple[str, ...]) -> dict[str, str]:
    aliases = {
        "-c": "ctx-size", "--ctx-size": "ctx-size",
        "-ngl": "gpu-layers", "--gpu-layers": "gpu-layers", "--n-gpu-layers": "gpu-layers",
        "-ts": "tensor-split", "--tensor-split": "tensor-split",
        "-ot": "override-tensor", "--override-tensor": "override-tensor",
    }
    values: dict[str, str] = {}
    for index, token in enumerate(argv[:-1]):
        if token in aliases:
            values[aliases[token]] = argv[index + 1]
    return values
