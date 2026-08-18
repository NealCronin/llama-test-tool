from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

from app.models.flags import FlagSpec

README_URL = "https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md"


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
            FlagSpec("--flash-attn", ("-fa", "--flash-attn"), "set Flash Attention use", 1, ("on|off|auto",), choices=("on", "off", "auto")),
            FlagSpec("--mmproj", ("-mm", "--mmproj"), "path to a multimodal projector file", 1, ("FILE",), special_editor="mmproj"),
            FlagSpec("--spec-draft-model", ("--spec-draft-model", "-md", "--model-draft"), "draft model for speculative decoding", 1, ("FNAME",), special_editor="draft_model"),
            FlagSpec("--spec-type", ("--spec-type",), "comma-separated list of speculative decoding types", 1, ("TYPE",), choices=("none", "draft-mtp", "draft-dflash", "draft-dspark", "ngram-mod"), special_editor="spec_type"),
            FlagSpec("--jinja", ("--jinja", "--no-jinja"), "whether to use jinja template engine", 0),
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
            choices = cls._choices(trailing, description)
            if not parameters and re.fullmatch(r"[\w.-]+(?:,[\w.-]+)+", trailing):
                parameters, choices = ["VALUE"], trailing.split(",")
            canonical = next((name for name in aliases if name.startswith("--")), aliases[0])
            special = cls._special_editor(canonical)
            specs.append(FlagSpec(
                canonical_name=canonical,
                aliases=aliases,
                description=description,
                parameter_count=len(parameters),
                parameter_names=tuple(parameters),
                optional_parameter=optional,
                choices=tuple(choices),
                value_type=cls._value_type(parameters, description),
                repeatable=any(word in description.casefold() for word in ("multiple", "comma-separated values", "add a control vector")),
                special_editor=special,
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
        optional = bool(re.search(r"\[[^]]+\]", trailing))
        chunks = re.findall(r"(?:<[^>]+>|\[[^]]+\]|\{[^}]+\}|[A-Z][A-Z0-9_,-]*)", trailing)
        values: list[str] = []
        for chunk in chunks:
            raw = chunk.strip("<>[]{}")
            if raw and not re.fullmatch(r"[a-z0-9_,|-]+", raw):
                values.extend(raw.replace(",", " ").split())
            elif raw and (chunk.startswith(("[", "{", "<")) or raw.isupper()):
                values.append(raw)
        return values, optional

    @staticmethod
    def _choices(trailing: str, description: str) -> list[str]:
        sources = [trailing, description]
        choices: list[str] = []
        for source in sources:
            for enclosed in re.findall(r"(?:\[|\{|\()([^\]\)}]+)(?:\]|\}|\))", source):
                candidates = re.split(r"[|,]", enclosed.replace("\\|", "|"))
                if 1 < len(candidates) <= 30 and all(re.fullmatch(r"[\w.+-]+", item.strip()) for item in candidates):
                    choices.extend(item.strip() for item in candidates)
            allowed = re.search(r"allowed values:\s*([^.(]+)", source, re.I)
            if allowed:
                choices.extend(item.strip() for item in allowed.group(1).split(",") if item.strip())
            templates = re.search(r"list of built-in templates:\s*([a-z0-9_, -]+)", source, re.I)
            if templates:
                choices.extend(item.strip() for item in templates.group(1).split(",") if item.strip())
        return list(dict.fromkeys(choices))

    @staticmethod
    def _value_type(parameters: list[str], description: str) -> str:
        if not parameters:
            return "boolean"
        if all(parameter in {"N", "INDEX", "PORT", "SEED", "SECONDS", "MiB"} for parameter in parameters):
            return "integer"
        if any("path" in word.casefold() for word in (" ".join(parameters), description)):
            return "path"
        return "string"

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
