"""The single llama-server command spelling used by this application."""

from pathlib import Path

SERVER_COMMAND = "Engines/llama.cpp/build-mixed/bin/Release/llama-server.exe"


def server_executable_path() -> Path:
    """Resolve the saved command spelling from the repository's parent directory."""
    return Path(__file__).resolve().parent.parent.parent / SERVER_COMMAND
