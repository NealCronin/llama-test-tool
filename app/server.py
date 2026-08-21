"""The fixed Engines build directory and its tool command spellings."""

from pathlib import Path

SERVER_COMMAND = "Engines/llama.cpp/build/bin/Release/llama-server.exe"

FIXED_BUILD_COMMANDS = {
    "llama-server": SERVER_COMMAND,
    "llama-fit-params": "Engines/llama.cpp/build/bin/Release/llama-fit-params.exe",
    "llama-bench": "Engines/llama.cpp/build/bin/Release/llama-bench.exe",
}


def fixed_build_dir() -> Path:
    """The fixed Engines build directory, resolved from the repository's parent."""
    return Path(__file__).resolve().parent.parent.parent / "Engines/llama.cpp/build/bin/Release"


def fixed_tool_path(tool: str) -> Path:
    """Resolve the fixed spelling of one Engines tool (llama-server, llama-fit-params, llama-bench)."""
    return Path(__file__).resolve().parent.parent.parent / FIXED_BUILD_COMMANDS[tool]


def server_executable_path() -> Path:
    """Resolve the saved command spelling from the repository's parent directory."""
    return fixed_tool_path("llama-server")
