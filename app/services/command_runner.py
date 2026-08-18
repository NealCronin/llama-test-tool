from __future__ import annotations

import socket
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from app.models.command import Command


class CommandRunner(QObject):
    output = Signal(str)
    state_changed = Signal(str)
    finished = Signal(int, QProcess.ExitStatus)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.errorOccurred.connect(self._on_error)
        self.process.finished.connect(self._on_finished)
        self._stopping = False

    @staticmethod
    def available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    @property
    def running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, command: Command, executable: str) -> None:
        if self.running:
            raise RuntimeError("A command is already running.")
        executable_path = Path(executable)
        if not executable or not executable_path.is_file():
            raise RuntimeError("Configure a valid llama-server executable before testing.")
        self._stopping = False
        port = str(self.available_port()) if any("${PORT}" in argument.values for argument in command.arguments) else None
        argv = command.argv(str(executable_path), port=port)
        self.output.emit(f"$ {command.rendered(str(executable_path), port=port)}\n")
        self.process.setProgram(argv[0])
        self.process.setArguments(argv[1:])
        self.process.start()
        self.state_changed.emit("Running")

    def stop(self) -> None:
        if not self.running:
            return
        self._stopping = True
        self.state_changed.emit("Stopping")
        self.process.terminate()
        QTimer.singleShot(2_000, self._kill_if_still_running)

    def _kill_if_still_running(self) -> None:
        if self.running:
            self.output.emit("Process did not terminate in two seconds; killing it.\n")
            self.process.kill()

    def _read_output(self) -> None:
        self.output.emit(bytes(self.process.readAllStandardOutput()).decode(errors="replace"))

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self.output.emit(f"Process error: {self.process.errorString()}\n")
        if not self._stopping:
            self.state_changed.emit("Error")

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        self._read_output()
        self.output.emit(f"\nProcess exited with code {exit_code}.\n")
        self.state_changed.emit("Stopped")
        self._stopping = False
        self.finished.emit(exit_code, status)
