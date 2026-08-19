"""Server verification models: runtime connection context and staged results.

The verifier answer is a structured, immutable record so a later Experiment
History phase can consume it without scraping UI text. No secret values are
ever stored here — authentication state is a boolean; the keys themselves live
only inside the verification request scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VerificationMode(str, Enum):
    GENERATION = "generation"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    UNSUPPORTED = "unsupported"


class VerificationTransport(str, Enum):
    TCP_HTTP = "tcp-http"
    HTTPS = "https"
    UNIX_SOCKET = "unix-socket"


@dataclass(frozen=True)
class ServerRunContext:
    """Exact runtime facts of the launched process (resolved from the command).

    ``argv`` is the process's final argv — ``${PORT}`` already substituted —
    while the builder's persistent Command remains untouched. Secrets are not
    part of this object.
    """

    executable: str
    argv: tuple[str, ...]
    rendered_redacted_command: str
    bind_host: str
    connect_host: str
    port: int
    api_prefix: str  # normalized: "" or "/foo" — never a trailing slash
    auth_available: bool
    mode: VerificationMode
    transport: VerificationTransport
    model_alias: str  # explicit --alias value ("" when not configured)


@dataclass(frozen=True)
class VerificationStageResult:
    index: int  # 1..4; 0 = preflight
    name: str  # process | ready | api | inference
    status: str  # passed | failed | skipped | pending
    detail: str = ""


@dataclass(frozen=True)
class ServerVerificationResult:
    """Immutable outcome of one Test Server run (current session only)."""

    started: bool
    ready: bool
    api_ok: bool
    inference_ok: bool | None  # None when Stage 4 was skipped or never reached
    failed_stage: str | None
    skipped_stage: str | None
    error_detail: str
    bind_host: str
    connect_host: str
    port: int
    api_prefix: str
    model_ids: tuple[str, ...]
    mode: VerificationMode
    process_start_ms: float | None
    ready_ms: float | None
    api_ms: float | None
    inference_ms: float | None
    generated_text: str
    prompt_tokens: int | float | None
    completion_tokens: int | float | None
    prompt_tps: float | None
    generation_tps: float | None
    exit_code: int | None
    log_tail: str
    stages: tuple[VerificationStageResult, ...] = ()

    @property
    def verified(self) -> bool:
        """All applicable stages passed; Stage 4 may legitimately be skipped."""
        return (
            self.started
            and self.ready
            and self.api_ok
            and self.failed_stage is None
            and (self.inference_ok is True or self.skipped_stage == "inference")
        )