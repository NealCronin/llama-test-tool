from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from app.models.command import Command, CommandArgument
from app.services.flag_catalog import FlagCatalog

SHELL_SYNTAX = re.compile(r"(?<!\\)[|&;`]|\$\((?!PORT\})|(?:^|\s)[<>](?:\s|$)")


@dataclass(frozen=True)
class ParseResult:
    command: Command | None
    raw_reason: str | None = None


def parse_command(text: str, catalog: FlagCatalog) -> ParseResult:
    """Parse a simple llama-server command; refuse shell syntax rather than corrupt it."""
    if not text.strip():
        return ParseResult(Command())
    if SHELL_SYNTAX.search(text):
        return ParseResult(None, "The command uses shell syntax and must remain in Raw Command Mode.")
    try:
        tokens = shlex.split(text.replace("\r\n", "\n"), posix=False)
    except ValueError as error:
        return ParseResult(None, f"Cannot parse command quoting: {error}")
    tokens = [token[1:-1] if len(token) >= 2 and token[:1] == token[-1:] == '"' else token for token in tokens]
    if not tokens:
        return ParseResult(Command())
    command = Command(executable=tokens[0])
    command.arguments = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            return ParseResult(None, f"Unexpected bare value {token!r}; the command cannot be safely mapped to flags.")
        spec = catalog.find(token)
        if spec is None:
            return ParseResult(None, f"Unknown flag {token!r}; preserving it requires Raw Command Mode.")
        count = spec.parameter_count
        if spec.optional_parameter and index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
            count = 1
        if index + count >= len(tokens):
            return ParseResult(None, f"{token} has incomplete values.")
        values = tokens[index + 1:index + 1 + count]
        command.arguments.append(CommandArgument(token, values, _source_type(spec.canonical_name)))
        index += count + 1
    command.ensure_model_argument()
    return ParseResult(command)


def _source_type(canonical_name: str) -> str:
    return {
        "--model": "model", "--mmproj": "mmproj", "--spec-draft-model": "draft_model",
        "--model-draft": "draft_model", "--chat-template-file": "template_file",
    }.get(canonical_name, "manual")
