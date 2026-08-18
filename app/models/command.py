from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class CommandArgument:
    flag: str
    values: list[str] = field(default_factory=list)
    source_type: str = "manual"
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CommandArgument":
        source_type = str(data.get("source_type", "manual"))
        metadata = {str(key): str(value) for key, value in data.get("metadata", {}).items()}
        if source_type == "draft_model":
            metadata.pop("draft_source", None)
        return cls(
            flag=str(data["flag"]), values=[str(value) for value in data.get("values", [])],
            source_type=source_type, metadata=metadata,
        )


@dataclass
class Command:
    executable: str = "llama-server"
    arguments: list[CommandArgument] = field(default_factory=lambda: [CommandArgument("-m", [""], "model")])

    def __post_init__(self) -> None:
        self.ensure_model_argument()

    def ensure_model_argument(self) -> CommandArgument:
        model = next((argument for argument in self.arguments if argument.flag in {"-m", "--model"}), None)
        if model is None:
            model = CommandArgument("-m", [""], "model")
            self.arguments.insert(0, model)
        if not model.values:
            model.values = [""]
        return model

    def argv(self, executable: str | None = None, *, port: str | None = None) -> list[str]:
        result = [executable or self.executable]
        for argument in self.arguments:
            if argument.source_type == "model_default_template":
                continue
            result.append(argument.flag)
            for value in argument.values:
                result.append(str(port) if value == "${PORT}" and port is not None else value)
        return result

    def rendered(self, executable: str | None = None, *, port: str | None = None, vertical: bool = False) -> str:
        argv = self.argv(executable, port=port)
        quoted = [subprocess.list2cmdline([part]) for part in argv]
        if vertical:
            return " ^\n  ".join(quoted)
        return " ".join(quoted)

    def rendered_lines(self, executable: str | None = None, *, port: str | None = None) -> str:
        """One argv token per line for llama-swap's documented YAML cmd block form."""
        return "\n".join(subprocess.list2cmdline([part]) for part in self.argv(executable, port=port))

    def to_dict(self) -> dict:
        return {"executable": self.executable, "arguments": [argument.to_dict() for argument in self.arguments]}

    @classmethod
    def from_dict(cls, data: dict) -> "Command":
        return cls(
            executable=str(data.get("executable", "llama-server")),
            arguments=[CommandArgument.from_dict(item) for item in data.get("arguments", [])],
        )

    def copy(self) -> "Command":
        return Command.from_dict(self.to_dict())

    def find(self, names: Iterable[str]) -> CommandArgument | None:
        return next((argument for argument in self.arguments if argument.flag in set(names)), None)

    def has_flag(self, names: Iterable[str]) -> bool:
        return self.find(names) is not None

    def reset(self) -> None:
        self.arguments = [CommandArgument("-m", [""], "model")]

    def model_path(self) -> Path | None:
        value = self.ensure_model_argument().values[0].strip()
        return Path(value) if value else None
