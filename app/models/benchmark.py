from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BenchmarkOptions:
    prompt_tokens: int = 512
    generation_tokens: int = 128
    repetitions: int = 5
    context_depth: int = 0
    delay: int = 0
    no_warmup: bool = False


@dataclass(frozen=True)
class BenchmarkArgumentTranslation:
    argv: tuple[str, ...]
    skipped_arguments: tuple[str, ...] = ()
    translated_arguments: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkTest:
    model: str = ""
    backend: str = ""
    gpu_info: str = ""
    test_type: str = ""
    prompt_tokens: int = 0
    generation_tokens: int = 0
    depth: int = 0
    tokens_per_second: float = 0.0
    stddev_tokens_per_second: float = 0.0
    batch: int = 0
    ubatch: int = 0
    threads: int = 0
    gpu_layers: int | None = None
    cpu_moe_layers: int | None = None
    split_mode: str = ""
    tensor_split: str = ""
    cache_type_k: str = ""
    cache_type_v: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkResult:
    tests: tuple[BenchmarkTest, ...] = ()
    skipped_arguments: tuple[str, ...] = ()
    translated_arguments: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    sent_argv: tuple[str, ...] = ()
    raw_stdout: str = ""
    raw_stderr: str = ""
    exit_code: int | None = None
    error: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and bool(self.tests) and not self.error

    @property
    def raw_output(self) -> str:
        return self.raw_stdout + ("\n" if self.raw_stdout and self.raw_stderr else "") + self.raw_stderr
