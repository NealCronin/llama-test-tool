"""Asynchronous server verification of a launched llama-server process.

CommandRunner owns the process; this service owns interpretation: staged
readiness/API/inference probing, timing, and the immutable result record.
HTTP runs through QNetworkAccessManager + QTimer (never blocking the GUI
thread), every run carries a generation token so a stale reply from a replaced
run can never mutate a newer one, and cancellation aborts replies, timers, and
the process.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app.models.command import Command
from app.models.server_verification import (
    ServerRunContext,
    ServerVerificationResult,
    VerificationMode,
    VerificationStageResult,
    VerificationTransport,
)
from app.services.command_runner import CommandRunner, resolve_server_context
from app.services.flag_catalog import FlagCatalog

POLL_INTERVAL_MS = 500
HTTP_TIMEOUT_MS = 10_000

_PROBE_PROMPT = "Reply with the word OK."
_PROBE_N_PREDICT = 16

STAGE_IDS = ("process", "ready", "api", "inference")


def mask_secrets(text: str, secrets: tuple[str, ...]) -> str:
    """Mask configured API keys anywhere they appear (defense in depth)."""
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "***")
    return text


def resolve_api_keys(command: Command, catalog: FlagCatalog) -> tuple[str, ...]:
    """Bearer keys for verification: ``--api-key`` (first comma key) >
    ``--api-key-file`` (first non-empty, non-comment line) > inherited
    ``LLAMA_API_KEY``. Raises OSError when a configured key file is unreadable.
    """
    key = next(
        (
            argument.values[0].strip()
            for argument in command.arguments
            if (spec := catalog.find(argument.flag)) is not None
            and spec.canonical_name == "--api-key"
            and argument.values
            and argument.values[0].strip()
        ),
        "",
    )
    if key:
        return tuple(part.strip() for part in key.split(",") if part.strip())
    key_file = next(
        (
            argument.values[0].strip()
            for argument in command.arguments
            if (spec := catalog.find(argument.flag)) is not None
            and spec.canonical_name == "--api-key-file"
            and argument.values
            and argument.values[0].strip()
        ),
        "",
    )
    if key_file:
        path = Path(key_file)
        if not path.is_file():
            raise OSError(f"API key file is not readable: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return (line,)
    env_key = os.environ.get("LLAMA_API_KEY")
    if env_key and env_key.strip():
        return (env_key.strip(),)
    return ()


def verification_url(host: str, port: int, prefix: str, path: str) -> str:
    """Build the loopback HTTP URL; IPv6 hosts get brackets."""
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{display_host}:{port}{prefix}{path}"


def timing_tps(per_second: object, per_ms: object) -> float | None:
    """Throughput tokens/sec from llama-server timings (per_ms is per millisecond)."""
    if per_second is not None:
        try:
            return round(float(per_second), 3)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    if per_ms is not None:
        try:
            return round(float(per_ms) * 1000.0, 3)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    return None


class ServerVerificationService(QObject):
    """Stages: process -> readiness -> model API -> capability probe."""

    stage_changed = Signal(str, str, str)  # stage name, status, detail
    completed = Signal(object)  # ServerVerificationResult

    def __init__(self, runner: CommandRunner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.runner = runner
        self._http = QNetworkAccessManager(self)
        self._generation = 0
        self._active = False
        self._replies: list[QNetworkReply] = []
        self._context: ServerRunContext | None = None
        self._keys: tuple[str, ...] = ()
        self._timeout_s = 180
        self._began_at = 0.0
        self._times: dict[str, float | None] = {"process": None, "ready": None, "api": None, "inference": None}
        self._pending_stage = ""
        self._exit_code: int | None = None
        self._model_ids: tuple[str, ...] = ()
        self._stage_records: dict[str, VerificationStageResult] = {}
        self._generated_text = ""
        self._prompt_n: int | float | None = None
        self._completion_n: int | float | None = None
        self._prompt_tps: float | None = None
        self._gen_tps: float | None = None
        self._failed_stage: str | None = None
        self._failed_message = ""
        self._skipped_stage: str | None = None
        self._run_token = -1
        self.runner.started.connect(self._on_runner_started)
        self.runner.finished.connect(self._on_runner_finished)
        self.runner.process_error.connect(self._on_runner_error)

    # ------------------------------------------------------------ public API
    @property
    def active(self) -> bool:
        return self._active

    def verify(self, command: Command, catalog: FlagCatalog, *, executable: str, timeout_seconds: int = 180) -> None:
        """Start a verification run, replacing any active one."""
        if self._active:
            self.cancel()
        self._generation += 1
        generation = self._generation
        self._active = True
        self._timeout_s = max(1, int(timeout_seconds))
        self._began_at = time.monotonic()
        self._times = {"process": None, "ready": None, "api": None, "inference": None}
        self._pending_stage = ""
        self._exit_code = None
        self._model_ids = ()
        self._stage_records = {}
        self._generated_text = ""
        self._prompt_n = self._completion_n = self._prompt_tps = self._gen_tps = None
        self._failed_stage = None
        self._failed_message = ""
        self._skipped_stage = None
        self._keys = ()
        self._run_token = -1  # drop any stale runner signals until the new start lands

        try:
            keys = resolve_api_keys(command, catalog)
        except OSError as error:
            self._fail(generation, "preflight", str(error), include_tail=False)
            return
        self._keys = keys
        self._context = resolve_server_context(command, catalog, executable=executable)
        for stage in STAGE_IDS:
            self._emit_stage(stage, "pending", "")
        try:
            # Pass the pre-resolved context so ${PORT} resolves to ONE port
            # shared by the verifier and the launched process.
            self.runner.start(command, executable, catalog, context=self._context)
        except RuntimeError as error:
            self._fail(generation, "preflight", str(error), include_tail=False)
            return
        self._run_token = self.runner.run_token
        self._emit_stage("process", "running", "Starting the server process…")

    def cancel(self) -> None:
        """Invalidate the run, abort in-flight HTTP, stop the process."""
        self._generation += 1
        self._active = False
        for reply in self._replies:
            try:
                reply.abort()
            except RuntimeError:
                pass
        self._replies.clear()
        self.runner.stop()
        self.stage_changed.emit("run", "cancelled", "Verification cancelled by user.")

    def _is_current(self, generation: int) -> bool:
        return self._active and generation == self._generation

    # ------------------------------------------------------------ runner events
    def _on_runner_started(self) -> None:
        # No token check here: QProcess.started may be delivered synchronously
        # inside runner.start() before the service captures the new run token,
        # and a cancelled run's late started is still dropped by _active.
        if not self._active:
            return
        self._times["process"] = round((time.monotonic() - self._began_at) * 1000, 1)
        context = self._context
        if context is not None and context.transport != VerificationTransport.TCP_HTTP:
            if context.transport == VerificationTransport.HTTPS:
                reason = "HTTPS"
            else:
                reason = "Unix socket"
            self._emit_stage("process", "passed", "Process started")
            self._emit_stage("ready", "skipped", "Verification transport unsupported")
            self._emit_stage("api", "skipped", "Verification transport unsupported")
            self._emit_stage("inference", "skipped", "Verification transport unsupported")
            self._publish(
                failed_stage=None,
                error_detail=f"Verification skipped: unsupported transport ({reason}). Server process left running — use the raw logs for manual checks.",
            )
            return
        self._emit_stage("process", "passed", "Process started")
        self._poll_health(self._generation)

    def _on_runner_finished(self, exit_code: int, _status) -> None:
        if not self._active or self.runner.run_token != self._run_token:
            return
        self._exit_code = exit_code
        stage = self._pending_stage or "ready"
        self._fail(self._generation, stage, f"Server process exited with code {exit_code}.", include_tail=True)

    def _on_runner_error(self, message: str) -> None:
        if not self._active or self.runner.run_token != self._run_token:
            return
        self._fail(self._generation, "process", f"Failed to start the server process: {message}", include_tail=True)

    # ------------------------------------------------------------ HTTP primitives
    def _request(self, generation: int, url: str, *, method: str = "GET", body: bytes | None = None) -> None:
        if not self._is_current(generation):
            return
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(HTTP_TIMEOUT_MS)
        if self._keys:
            request.setRawHeader(b"Authorization", f"Bearer {self._keys[0]}".encode())
        try:
            if method == "GET":
                reply = self._http.get(request)
            else:
                reply = self._http.post(request, body or b"")
        except RuntimeError:
            self._fail(generation, self._pending_stage or "ready", "Failed to issue an HTTP request.", include_tail=False)
            return
        self._replies.append(reply)
        reply.finished.connect(lambda: self._on_reply(generation, reply))

    def _on_reply(self, generation: int, reply: QNetworkReply) -> None:
        if reply in self._replies:
            self._replies.remove(reply)
        try:
            data = bytes(reply.readAll().data()).decode(errors="replace")
            status_value = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            status = int(status_value) if status_value is not None else -1
            error = reply.error()
        except RuntimeError:
            status, data, error = -1, "", QNetworkReply.NetworkError.OperationCanceledError
        reply.deleteLater()
        if not self._is_current(generation):
            return
        handler = {"ready": self._on_ready, "api": self._on_models, "inference": self._on_probe}.get(self._pending_stage)
        if handler is not None:
            handler(generation, status, error, data)

    def _redact(self, text: str) -> str:
        return mask_secrets(text, self._keys)

    def _emit_stage(self, stage: str, status: str, detail: str) -> None:
        index = {"process": 0, "ready": 1, "api": 2, "inference": 3}.get(stage, 0)
        self._stage_records[stage] = VerificationStageResult(index, stage, status, detail)
        self.stage_changed.emit(stage, status, detail)

    # ------------------------------------------------------------ Stage 2: readiness
    def _poll_health(self, generation: int) -> None:
        if not self._is_current(generation):
            return
        elapsed = time.monotonic() - self._began_at
        if elapsed >= self._timeout_s:
            self._fail(generation, "ready", f"Server did not become ready within {self._timeout_s} seconds.", include_tail=True)
            return
        self._pending_stage = "ready"
        self._emit_stage("ready", "running", f"Waiting for model readiness… ({int(elapsed)}s)")
        context = self._context
        if context is None:
            return
        self._request(generation, verification_url(context.connect_host, context.port, context.api_prefix, "/health"))

    def _on_ready(self, generation: int, status: int, _error, data: str) -> None:
        if status == 200:
            self._times["ready"] = round((time.monotonic() - self._began_at) * 1000, 1)
            self._emit_stage("ready", "passed", "Server ready")
            self._pending_stage = "api"
            self._emit_stage("api", "running", "Requesting /v1/models…")
            context = self._context
            if context is not None:
                self._request(generation, verification_url(context.connect_host, context.port, context.api_prefix, "/v1/models"))
            return
        if status == 503 or status <= 0:
            # Still loading (503) or connection refused during startup: keep polling.
            QTimer.singleShot(POLL_INTERVAL_MS, lambda: self._poll_health(generation))
            return
        detail = self._redact(data.strip()[:240])
        self._fail(generation, "ready", f"Unexpected /health response: HTTP {status}{' — ' + detail if detail else ''}", include_tail=False)

    # ------------------------------------------------------------ Stage 3: model API
    def _on_models(self, generation: int, status: int, _error, data: str) -> None:
        if status != 200:
            detail = self._redact(data.strip()[:240])
            self._fail(generation, "api", f"/v1/models returned HTTP {status}{' — ' + detail if detail else ''}", include_tail=False)
            return
        try:
            payload = json.loads(data)
        except (ValueError, TypeError):
            self._fail(generation, "api", "/v1/models returned invalid JSON.", include_tail=False)
            return
        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list) or not models:
            self._fail(generation, "api", "/v1/models response has no non-empty 'data' list.", include_tail=False)
            return
        model_ids = tuple(str(entry.get("id")) for entry in models if isinstance(entry, dict) and entry.get("id"))
        if not model_ids:
            self._fail(generation, "api", "/v1/models returned model entries without IDs.", include_tail=False)
            return
        self._times["api"] = round((time.monotonic() - self._began_at) * 1000, 1)
        self._model_ids = model_ids
        self._emit_stage("api", "passed", "API reachable")
        context = self._context
        if context is not None and context.model_alias and context.model_alias not in model_ids:
            self._fail(generation, "api", f"Configured --alias {context.model_alias!r} is not among served model IDs: {', '.join(model_ids)}.", include_tail=False)
            return
        self._run_probe(generation)

    # ------------------------------------------------------------ Stage 4: capability probe
    def _run_probe(self, generation: int) -> None:
        context = self._context
        if context is None:
            return
        base = verification_url(context.connect_host, context.port, context.api_prefix, "")
        self._pending_stage = "inference"
        if context.mode is VerificationMode.GENERATION:
            self._emit_stage("inference", "running", "Sending a tiny completion probe…")
            payload = {"prompt": _PROBE_PROMPT, "n_predict": _PROBE_N_PREDICT, "temperature": 0, "stream": False}
            self._request(generation, base + "/completion", method="POST", body=json.dumps(payload).encode())
        elif context.mode is VerificationMode.EMBEDDING:
            self._emit_stage("inference", "running", "Sending a tiny embedding probe…")
            self._request(generation, base + "/embedding", method="POST", body=json.dumps({"content": "hello"}).encode())
        else:
            self._emit_stage("inference", "running", "Sending a tiny reranking probe…")
            payload = {"query": "hello", "texts": ["a horse", "An LLM llama"]}
            self._request(generation, base + "/rerank", method="POST", body=json.dumps(payload).encode())

    def _on_probe(self, generation: int, status: int, _error, data: str) -> None:
        context = self._context
        if context is None:
            return
        if status == 404:
            self._skip_inference(f"HTTP 404 on the {context.mode.value} probe endpoint — the server does not expose it; verification for this mode is unsupported.")
            return
        if status != 200:
            detail = self._redact(data.strip()[:240])
            self._fail(generation, "inference", f"Probe request returned HTTP {status}{' — ' + detail if detail else ''}", include_tail=False)
            return
        try:
            payload = json.loads(data)
        except (ValueError, TypeError):
            self._fail(generation, "inference", "Probe response was not valid JSON.", include_tail=False)
            return
        if context.mode is VerificationMode.GENERATION:
            if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
                self._fail(generation, "inference", "Completion response has no text content.", include_tail=False)
                return
            self._generated_text = payload["content"]
            timings = payload.get("timings") if isinstance(payload, dict) else None
            if isinstance(timings, dict):
                self._prompt_n = int(timings["prompt_n"]) if isinstance(timings.get("prompt_n"), (int, float)) else None
                self._completion_n = int(timings["predicted_n"]) if isinstance(timings.get("predicted_n"), (int, float)) else None
                self._prompt_tps = timing_tps(timings.get("prompt_per_second"), timings.get("prompt_per_ms"))
                self._gen_tps = timing_tps(timings.get("predicted_per_second"), timings.get("predicted_per_ms"))
        elif context.mode is VerificationMode.EMBEDDING:
            if not isinstance(payload, dict) or not isinstance(payload.get("embedding"), list):
                self._fail(generation, "inference", "Embedding response is structurally invalid.", include_tail=False)
                return
        elif context.mode is VerificationMode.RERANK:
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                self._fail(generation, "inference", "Rerank response is structurally invalid.", include_tail=False)
                return
        self._times["inference"] = round((time.monotonic() - self._began_at) * 1000, 1)
        self._emit_stage("inference", "passed", "Inference probe succeeded")
        self._publish(failed_stage=None, error_detail="")

    def _skip_inference(self, reason: str) -> None:
        self._emit_stage("inference", "skipped", reason)
        self._skipped_stage = "inference"
        self._publish(failed_stage=None, error_detail=reason)

    # ------------------------------------------------------------ publish
    def _fail(self, generation: int, stage: str, message: str, *, include_tail: bool) -> None:
        if not self._is_current(generation):
            return
        self._emit_stage(stage, "failed", message)
        detail = message
        if include_tail:
            tail = self._redact(self.runner.log_tail)
            if tail:
                detail += "\n\nLast server output:\n" + tail
        self._failed_stage = stage
        self._failed_message = message
        self._publish(failed_stage=stage, error_detail=detail)

    def _publish(self, *, failed_stage: str | None, error_detail: str) -> None:
        if not self._active:
            return
        self._active = False
        context = self._context
        if context is None:
            return
        records = tuple(
            record
            for record in (self._stage_records.get(stage) for stage in STAGE_IDS)
            if record is not None
        )
        started = self._times["process"] is not None
        ready = self._times["ready"] is not None
        api_ok = self._times["api"] is not None
        inference_ok = True if self._times["inference"] is not None else None
        if self._skipped_stage == "inference":
            inference_ok = None
        result = ServerVerificationResult(
            started=started,
            ready=ready,
            api_ok=api_ok,
            inference_ok=inference_ok,
            failed_stage=failed_stage,
            skipped_stage=self._skipped_stage,
            error_detail=error_detail,
            bind_host=context.bind_host,
            connect_host=context.connect_host,
            port=context.port,
            api_prefix=context.api_prefix,
            model_ids=self._model_ids,
            mode=context.mode,
            process_start_ms=self._times["process"],
            ready_ms=self._times["ready"],
            api_ms=self._times["api"],
            inference_ms=self._times["inference"],
            generated_text=self._generated_text,
            prompt_tokens=self._prompt_n,
            completion_tokens=self._completion_n,
            prompt_tps=self._prompt_tps,
            generation_tps=self._gen_tps,
            exit_code=self._exit_code,
            log_tail=self._redact(self.runner.log_tail),
            stages=records,
        )
        self.completed.emit(result)