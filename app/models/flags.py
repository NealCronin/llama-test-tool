from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


NEGATIVE_SHORT_ALIASES = frozenset({"-nkvo", "-nr", "-ndio", "-nocb", "-no-kvu", "-nopo", "-nmmproj"})

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
    positive_aliases: tuple[str, ...] = ()
    negative_aliases: tuple[str, ...] = ()

    @property
    def preferred_name(self) -> str:
        preferred = {
            "--model": "-m", "--mmproj": "-mm", "--spec-draft-model": "-md",
            "--gpu-layers": "-ngl", "--ctx-size": "-c", "--batch-size": "-b",
            "--ubatch-size": "-ub", "--parallel": "-np", "--flash-attn": "-fa",
            "--split-mode": "-sm", "--tensor-split": "-ts", "--device": "-dev",
            "--main-gpu": "-mg", "--cpu-moe": "-cmoe", "--n-cpu-moe": "-ncmoe",
            "--cache-type-k": "-ctk", "--cache-type-v": "-ctv",
        }
        candidate = preferred.get(self.canonical_name, self.canonical_name)
        return candidate if candidate in self.selectable_aliases else self.selectable_aliases[0]

    def is_negative(self, flag: str) -> bool:
        return flag in self.negative_aliases

    @property
    def selectable_aliases(self) -> tuple[str, ...]:
        return self.positive_aliases or tuple(alias for alias in self.aliases if alias not in self.negative_aliases) or self.aliases

    def matches(self, query: str) -> bool:
        words = query.casefold().split()
        haystack = " ".join((self.canonical_name, *self.aliases, self.description)).casefold()
        return all(word in haystack for word in words)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("aliases", "parameter_names", "choices", "positive_aliases", "negative_aliases"):
            data[key] = list(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlagSpec":
        aliases = tuple(data.get("aliases", []))
        negative = tuple(dict.fromkeys((*tuple(data.get("negative_aliases", ())), *_negative_aliases(aliases))))
        positive = tuple(alias for alias in aliases if alias not in negative)
        return cls(
            canonical_name=data["canonical_name"],
            aliases=aliases,
            description=data.get("description", ""),
            parameter_count=int(data.get("parameter_count", 0)),
            parameter_names=tuple(data.get("parameter_names", [])),
            optional_parameter=bool(data.get("optional_parameter", False)),
            choices=tuple(data.get("choices", [])),
            value_type=data.get("value_type", "string"),
            repeatable=bool(data.get("repeatable", False)),
            special_editor=data.get("special_editor"),
            positive_aliases=positive,
            negative_aliases=negative,
        )
def _negative_aliases(aliases: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(alias for alias in aliases if alias.startswith(("--no-", "-no-")) or alias in NEGATIVE_SHORT_ALIASES)
