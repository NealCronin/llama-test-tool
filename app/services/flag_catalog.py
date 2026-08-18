from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

from app.models.flags import NEGATIVE_SHORT_ALIASES, FlagSpec

README_URL = "https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md"

COMMON_FLAG_REFERENCES = frozenset({
    "--model", "--ctx-size", "--parallel", "--gpu-layers", "--device", "--split-mode", "--tensor-split", "--main-gpu", "--override-tensor",
    "--cpu-moe", "--n-cpu-moe", "--kv-offload", "--no-kv-offload", "--op-offload", "--no-op-offload", "--load-mode", "--no-host", "--repack",
    "--cache-type-k", "--cache-type-v", "--kv-unified", "--cache-ram", "--ctx-checkpoints", "--threads", "--threads-batch", "--batch-size",
    "--ubatch-size", "--flash-attn", "--fit", "--fit-target", "--fit-ctx", "--mmproj", "--mmproj-offload", "--no-mmproj-offload",
    "--image-min-tokens", "--image-max-tokens", "--mtmd-batch-max-tokens", "--spec-type", "--spec-draft-model", "--cache-type-k-draft",
    "--cache-type-v-draft", "--spec-draft-type-k", "--spec-draft-type-v", "--spec-draft-device", "--spec-draft-ngl", "--spec-draft-override-tensor",
    "--spec-draft-cpu-moe", "--spec-draft-n-cpu-moe", "--spec-draft-n-max", "--spec-draft-n-min", "--spec-draft-p-min", "--spec-draft-p-split",
    "--spec-ngram-mod-n-match", "--spec-ngram-mod-n-min", "--spec-ngram-mod-n-max", "--host", "--port", "--alias", "--timeout", "--threads-http",
    "--jinja", "--no-jinja", "--chat-template", "--chat-template-file", "--reasoning", "--reasoning-format", "--reasoning-effort", "--reasoning-budget",
    "--temp", "--top-p", "--top-k", "--min-p", "--presence-penalty", "--frequency-penalty", "--repeat-penalty", "--verbosity", "--log-verbosity", "--log-timestamps",
})


class FlagCatalog:
    def __init__(self, specs: list[FlagSpec], source: str = "bundled") -> None:
        self.specs = specs
        self.source = source
        self._by_alias = {alias: spec for spec in specs for alias in spec.aliases}

    @classmethod
    def load_bundled(cls, path: Path) -> "FlagCatalog":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return cls([FlagSpec.from_dict(item) for item in payload["flags"]], payload.get("source", "bundled"))
        except (OSError, ValueError, KeyError):
            return cls(cls.fallback_specs(), "minimal fallback")

    @staticmethod
    def fallback_specs() -> list[FlagSpec]:
        return [
            FlagSpec("--model", ("-m", "--model"), "model path to load", 1, ("FNAME",), special_editor="model"),
            FlagSpec("--ctx-size", ("-c", "--ctx-size"), "size of the prompt context", 1, ("N",), value_type="integer"),
            FlagSpec("--flash-attn", ("-fa", "--flash-attn"), "set Flash Attention use", 1, ("on|off|auto",), optional_parameter=True, choices=("on", "off", "auto")),
            FlagSpec("--mmproj", ("-mm", "--mmproj"), "path to a multimodal projector file", 1, ("FILE",), special_editor="mmproj"),
            FlagSpec("--spec-draft-model", ("--spec-draft-model", "-md", "--model-draft"), "draft model for speculative decoding", 1, ("FNAME",), special_editor="draft_model"),
            FlagSpec("--spec-type", ("--spec-type",), "comma-separated list of speculative decoding types", 1, ("TYPE",), choices=("none", "draft-mtp", "draft-dflash", "draft-dspark", "ngram-mod"), special_editor="spec_type"),
            FlagSpec("--jinja", ("--jinja", "--no-jinja"), "whether to use jinja template engine", 0, positive_aliases=("--jinja",), negative_aliases=("--no-jinja",)),
            FlagSpec("--chat-template", ("--chat-template",), "set custom jinja chat template", 1, ("JINJA_TEMPLATE",), special_editor="chat_template"),
            FlagSpec("--chat-template-file", ("--chat-template-file",), "set custom jinja chat template file", 1, ("JINJA_TEMPLATE_FILE",), special_editor="template_file"),
        ]

    @classmethod
    def parse_readme(cls, markdown: str) -> "FlagCatalog":
        specs: list[FlagSpec] = []
        for line in markdown.splitlines():
            match = re.match(r"^\| `(?P<argument>[^`]+)` \| (?P<description>.+?) \|$", line)
            if not match:
                continue
            argument = match.group("argument")
            aliases = tuple(re.findall(r"(?<!\w)(?:--?[A-Za-z][\w-]*)", argument))
            if not aliases:
                continue
            description = cls._clean_description(match.group("description"))
            trailing = argument[argument.rfind(aliases[-1]) + len(aliases[-1]):].strip()
            parameters, optional = cls._parameters(trailing)
            canonical = next((name for name in aliases if name.startswith("--") and not name.startswith("--no-")), aliases[0])
            choices = cls._choices(trailing, description)
            if canonical == "--gpu-layers":
                choices = list(dict.fromkeys([*choices, "auto", "all"]))
            positive_aliases, negative_aliases = cls._polarity(aliases)
            special = cls._special_editor(canonical)
            specs.append(FlagSpec(
                canonical_name=canonical,
                aliases=aliases,
                description=description,
                parameter_count=len(parameters),
                parameter_names=tuple(parameters),
                optional_parameter=optional,
                choices=tuple(choices),
                value_type=cls._value_type(canonical, parameters, choices, description),
                repeatable=any(word in description.casefold() for word in ("multiple", "comma-separated values", "add a control vector")),
                special_editor=special,
                positive_aliases=positive_aliases,
                negative_aliases=negative_aliases,
            ))
        # The README table deliberately has one row per logical flag. Deduplicate in case sections repeat it.
        unique = {spec.canonical_name: spec for spec in specs}
        return cls(list(unique.values()), "llama.cpp server README")

    @staticmethod
    def _clean_description(value: str) -> str:
        value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
        value = re.sub(r"<[^>]+>", "", value)
        value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
        return html.unescape(value.replace("\\|", "|").strip())

    @staticmethod
    def _parameters(trailing: str) -> tuple[list[str], bool]:
        """The README's suffix denotes argv tokens; punctuation stays inside one token."""
        if not trailing:
            return [], False
        optional = bool(re.fullmatch(r"\[[^\]]+\]", trailing))
        # Bracketed, braced, and angle grammars describe one argv value even when
        # they contain commas, pipes, nested brackets, equals signs, or spaces.
        if trailing.startswith(("<", "[", "{")):
            return [trailing.strip("<>[]{}") or "VALUE"], optional
        # Current server documentation has one genuine two-value option:
        # --control-vector-layer-range START END. Other bare grammars, including
        # lo-hi and N0,N1,..., occupy one command-line token.
        tokens = trailing.split()
        return tokens, False

    @staticmethod
    def _choices(trailing: str, description: str) -> list[str]:
        choices: list[str] = []
        grammar = trailing.strip()
        if grammar.startswith("{") and grammar.endswith("}"):
            choices = grammar[1:-1].split(",")
        elif grammar.startswith("[") and grammar.endswith("]"):
            choices = grammar[1:-1].replace("\\|", "|").split("|")
        elif re.fullmatch(r"[a-z][a-z0-9_-]*(?:,[a-z][a-z0-9_-]*)+", grammar):
            choices = grammar.split(",")
        else:
            allowed = re.search(r"allowed values:\s*([a-z0-9_+.-]+(?:\s*,\s*[a-z0-9_+.-]+)+)", description, re.I)
            if allowed:
                choices = allowed.group(1).split(",")
            templates = re.search(r"list of built-in templates:\s*([a-z0-9_, -]+)", description, re.I)
            if templates:
                choices = templates.group(1).split(",")
        return list(dict.fromkeys(choice.strip() for choice in choices if re.fullmatch(r"[\w.+-]+", choice.strip())))

    @staticmethod
    def _value_type(canonical: str, parameters: list[str], choices: list[str], description: str) -> str:
        if not parameters:
            return "boolean"
        if canonical == "--gpu-layers":
            return "integer_or_choices"
        if all(parameter in {"N", "INDEX", "PORT", "SEED", "SECONDS", "MiB"} for parameter in parameters):
            return "integer"
        if any("path" in word.casefold() for word in (" ".join(parameters), description)):
            return "path"
        return "string"

    @staticmethod
    def _polarity(aliases: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        negative = tuple(alias for alias in aliases if alias.startswith(("--no-", "-no-")) or alias in NEGATIVE_SHORT_ALIASES)
        return tuple(alias for alias in aliases if alias not in negative), negative

    @staticmethod
    def _special_editor(canonical: str) -> str | None:
        return {
            "--model": "model", "--mmproj": "mmproj", "--spec-draft-model": "draft_model",
            "--model-draft": "draft_model", "--chat-template": "chat_template",
            "--chat-template-file": "template_file", "--spec-type": "spec_type",
        }.get(canonical)

    def find(self, name: str) -> FlagSpec | None:
        return self._by_alias.get(name)

    def search(self, query: str) -> list[FlagSpec]:
        query = query.strip()
        if not query:
            return self.specs
        return [spec for spec in self.specs if spec.matches(query)]

    def common_specs(self) -> list[FlagSpec]:
        canonical = {spec.canonical_name for reference in COMMON_FLAG_REFERENCES if (spec := self.find(reference))}
        return [spec for spec in self.specs if spec.canonical_name in canonical]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"source": self.source, "flags": [spec.to_dict() for spec in self.specs]}, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def refresh(cls, timeout: int = 20) -> "FlagCatalog":
        request = Request(README_URL, headers={"User-Agent": "llama-test-tool"})
        with urlopen(request, timeout=timeout) as response:
            catalog = cls.parse_readme(response.read().decode("utf-8"))
        if not catalog.specs:
            raise ValueError("The upstream README did not contain a parseable flag table.")
        return catalog
