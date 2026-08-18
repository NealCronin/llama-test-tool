from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FlagSpec:
    """One logical llama-server argument, including every documented alias."""

    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    parameter_count: int = 0
    parameter_names: tuple[str, ...] = ()
    optional_parameter: bool = False
    choices: tuple[str, ...] = ()
    value_type: str = "string"
    repeatable: bool = False
    special_editor: str | None = None

    @property
    def preferred_name(self) -> str:
        preferred = {
            "--model": "-m", "--mmproj": "-mm", "--spec-draft-model": "-md",
            "--gpu-layers": "-ngl", "--ctx-size": "-c", "--batch-size": "-b",
            "--ubatch-size": "-ub", "--parallel": "-np", "--flash-attn": "-fa",
            "--split-mode": "-sm", "--tensor-split": "-ts",
        }
        return preferred.get(self.canonical_name, self.canonical_name)

    def matches(self, query: str) -> bool:
        words = query.casefold().split()
        haystack = " ".join((self.canonical_name, *self.aliases, self.description)).casefold()
        return all(word in haystack for word in words)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("aliases", "parameter_names", "choices"):
            data[key] = list(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlagSpec":
        return cls(
            canonical_name=data["canonical_name"],
            aliases=tuple(data.get("aliases", [])),
            description=data.get("description", ""),
            parameter_count=int(data.get("parameter_count", 0)),
            parameter_names=tuple(data.get("parameter_names", [])),
            optional_parameter=bool(data.get("optional_parameter", False)),
            choices=tuple(data.get("choices", [])),
            value_type=data.get("value_type", "string"),
            repeatable=bool(data.get("repeatable", False)),
            special_editor=data.get("special_editor"),
        )
