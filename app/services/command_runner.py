from __future__ import annotations

import os
import socket
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from app.models.command import Command
from app.models.server_verification import (
    ServerRunContext,
    VerificationMode,
    VerificationTransport,
)
from app.services.flag_catalog import FlagCatalog

_LOG_TAIL_LINES = 50
_LOG_TAIL_BYTES = 16 * 1024

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080


def api_prefix_normalized(raw: str) -> str:
    """Normalize an API prefix once: blank -> "", "foo" -> "/foo", "/foo" -> "/foo"."""
    text = (raw or "").strip()
    if not text:
        return ""
    if not text.startswith("/"):
        text = "/" + text
    return text.rstrip("/")


def render_redacted_argv(argv: tuple[str, ...]) -> str:
    """Render argv for display with ``--api-key`` values masked.

    Execution always uses the raw argv list; only the display string masks the
    secret. Values are also masked anywhere they appear beyond the flag token.
    """
    parts: list[str] = []
    secret_values: list[str] = []
    for index, token in enumerate(argv):
        if token in {"--api-key"} and index + 1 < len(argv):
            secret_values.append(argv[index + 1])
    for index, token in enumerate(argv):
        if token in {"--api-key"} and index + 1 < len(argv):
            parts.append(token)
            parts.append("***")
            continue
        if any(token == secret for secret in secret_values):
            parts.append("***")
            continue
        parts.append(token)
    import subprocess

    return " ".join(subprocess.list2cmdline([part]) for part in parts)


def _argument_value(command: Command, catalog: FlagCatalog, canonical_names: set[str]) -> str | None:
    for argument in command.arguments:
        spec = catalog.find(argument.flag)
        if spec is not None and spec.canonical_name in canonical_names and argument.values:
            return argument.values[0].strip()
    return None


def _has_flag(command: Command, catalog: FlagCatalog, canonical_names: set[str]) -> bool:
    for argument in command.arguments:
        spec = catalog.find(argument.flag)
        if spec is not None and spec.canonical_name in canonical_names:
            return True
    return False


def resolve_server_context(command: Command, catalog: FlagCatalog, *, executable: str) -> ServerRunContext:
    """Resolve EXACTLY where the launched process will serve, from the
    structured Command + FlagCatalog. Never mutates the command.

    Port cases:
      A. ``--port ${PORT}`` -> ephemeral port chosen here, substituted in argv.
      B. explicit numeric ``--port N`` -> that exact port.
      C. no port argument -> llama-server's effective default 8080 (no argv appended).
    """
    executable_path = executable or command.executable
    bind_host = _argument_value(command, catalog, {"--host"}) or _DEFAULT_HOST

    ssl_cert = _has_flag(command, catalog, {"--ssl-cert-file"})
    ssl_key = _has_flag(command, catalog, {"--ssl-key-file"})
    if bind_host.endswith(".sock"):
        transport = VerificationTransport.UNIX_SOCKET
    elif ssl_cert and ssl_key:
        transport = VerificationTransport.HTTPS
    else:
        transport = VerificationTransport.TCP_HTTP

    port_token = _argument_value(command, catalog, {"--port"})
    port_override: str | None = None
    if port_token == "${PORT}":
        port = CommandRunner.available_port()
        port_override = str(port)
    elif port_token:
        try:
            port = int(port_token)
        except ValueError:
            port = _DEFAULT_PORT
    else:
        port = _DEFAULT_PORT

    if bind_host in ("0.0.0.0", ""):
        connect_host = "127.0.0.1"
    elif bind_host == "::":
        connect_host = "::1"
    else:
        connect_host = bind_host

    api_prefix = _argument_value(command, catalog, {"--api-prefix"})
    prefix = api_prefix_normalized(api_prefix) if api_prefix else ""

    model_alias = _argument_value(command, catalog, {"--alias"}) or ""

    if _has_flag(command, catalog, {"--rerank", "--reranking"}):
        mode = VerificationMode.RERANK
    elif _has_flag(command, catalog, {"--embedding", "--embeddings"}):
        mode = VerificationMode.EMBEDDING
    else:
        mode = VerificationMode.GENERATION

    auth_available = bool(
        _argument_value(command, catalog, {"--api-key"})
        or _argument_value(command, catalog, {"--api-key-file"})
        or os.environ.get("LLAMA_API_KEY")
    )

    argv = tuple(command.argv(executable, port=port_override))
    return ServerRunContext(
        executable=executable,
        argv=argv,
        rendered_redacted_command=render_redacted_argv(argv),
        bind_host=bind_host,
        connect_host=connect_host,
        port=port,
        api_prefix=prefix,
        auth_available=auth_available,
        mode=mode,
        transport=transport,
        model_alias=model_alias,
    )


class CommandRunner(QObject):
    """QProcess lifecycle for the Server Test workflow.

    Responsibilities: exact runtime argv, process start/stop, stdout/stderr
    passthrough (plus a bounded in-memory tail for verification error
    reporting), and the resolved runtime connection context. This class never
    performs HTTP work — ServerVerificationService owns all interpretation.
    """

    output = Signal(str)
    state_changed = Signal(str)
    started = Signal()  # QProcess.started: the process actually launched
    process_error = Signal(str)  # failed to start / device error
    finished = Signal(int, QProcess.ExitStatus)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.errorOccurred.connect(self._on_error)
        self.process.started.connect(self._on_started)
        self.process.finished.connect(self._on_finished)
        self.context: ServerRunContext | None = None
        self._run_token = 0
        self._log_tail: deque[str] = deque(maxlen=_LOG_TAIL_LINES)
        self._tail_bytes = 0
        self._stopping = False

    @property
    def run_token(self) -> int:
        """Incremented per successful start; lets callers drop stale signals."""
        return self._run_token

    @staticmethod
    def available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    @staticmethod
    def port_in_use(host: str, port: int) -> bool:
        """Best-effort liveness check: something already listening on host:port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            try:
                result = probe.connect_ex((host, port))
                return result == 0
            except OSError:
                return False

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    @property
    def log_tail(self) -> str:
        """Bounded recent process output for verification error reporting."""
        return "\n".join(self._log_tail)

    def start(self, command: Command, executable: str, catalog: FlagCatalog, context: ServerRunContext | None = None) -> None:
        """Resolve the runtime context and launch the process.

        ``context`` may be pre-resolved by the caller (mandatory for ``${PORT}``
        so the caller and the process agree on one ephemeral port). ``${PORT}``
        is substituted here and only here; the builder's persistent Command is
        never mutated. An explicit/default TCP port that is already occupied
        raises before launch so a stale server can never be verified.
        """
        if self.running:
            # Replacement semantics: fully settle the previous process before
            # the new one so its finished/stderr signals can never leak into
            # the new run. Bounded wait: terminate, then kill.
            self.process.terminate()
            self.process.waitForFinished(1500)
            if self.process.state() != QProcess.ProcessState.NotRunning:
                self.process.kill()
                self.process.waitForFinished(1500)
        executable_path = Path(executable)
        if not executable or not executable_path.is_file():
            raise RuntimeError("Configure a valid llama-server executable before testing.")
        if context is None:
            context = resolve_server_context(command, catalog, executable=executable)
        if context.transport == VerificationTransport.TCP_HTTP and CommandRunner.port_in_use(context.connect_host, context.port):
            raise RuntimeError(f"Port {context.port} is already in use; server verification would be ambiguous.")
        self._stopping = False
        self._reset_tail()
        self._run_token += 1
        self.context = context
        self.output.emit(f"$ {context.rendered_redacted_command}\n")
        self.process.setProgram(context.argv[0])
        self.process.setArguments(list(context.argv[1:]))
        self.state_changed.emit("Starting")
        self.process.start()

    def stop(self) -> None:
        if not self.running:
            return
        self._stopping = True
        self.state_changed.emit("Stopping")
        self.process.terminate()
        token = self._run_token
        QTimer.singleShot(2_000, lambda: self._kill_if_still_running(token))

    def _kill_if_still_running(self, token: int) -> None:
        if token != self._run_token:
            return  # a newer run replaced this stop; never touch it
        if self.running:
            self.output.emit("Process did not terminate in two seconds; killing it.\n")
            self.process.kill()

    def _read_output(self) -> None:
        data = bytes(self.process.readAllStandardOutput().data()).decode(errors="replace")
        if not data:
            return
        self._append_tail(data)
        self.output.emit(data)

    def _append_tail(self, text: str) -> None:
        for line in text.splitlines():
            if not line.strip():
                continue
            while self._tail_bytes + len(line) + 1 > _LOG_TAIL_BYTES and self._log_tail:
                evicted = self._log_tail.popleft()
                self._tail_bytes -= len(evicted) + 1
            self._log_tail.append(line)
            self._tail_bytes += len(line) + 1

    def _reset_tail(self) -> None:
        self._log_tail.clear()
        self._tail_bytes = 0

    def _on_started(self) -> None:
        self.state_changed.emit("Running")
        self.started.emit()

    def _on_error(self, _process_error: QProcess.ProcessError) -> None:
        self.output.emit(f"Process error: {self.process.errorString()}\n")
        self.process_error.emit(self.process.errorString() or "unknown process error")
        if not self._stopping:
            self.state_changed.emit("Error")

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        self._read_output()
        self.output.emit(f"\nServer process exited with code {exit_code}.\n")
        self.state_changed.emit("Stopped")
        self._stopping = False
        self.finished.emit(exit_code, status)