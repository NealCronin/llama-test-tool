from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq


class LlamaSwapSchemaError(ValueError):
    pass


def _plain(value: Any) -> Any:
    if isinstance(value, (CommentedMap, dict)):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (CommentedSeq, list, tuple)):
        return [_plain(item) for item in value]
    return value


class LlamaSwapConfigValidator:
    """Official-schema validation without mutating round-trip YAML objects."""
    def __init__(self, schema_path: Path | None = None) -> None:
        path = schema_path or Path(__file__).parents[2] / "data" / "llama_swap_config_schema.json"
        self.schema = json.loads(path.read_text(encoding="utf-8"))

    def validate(self, data: Mapping[str, Any]) -> None:
        try:
            from jsonschema import Draft7Validator
        except ImportError as error:
            raise LlamaSwapSchemaError("jsonschema is required to validate llama-swap configuration. Install requirements.txt.") from error
        errors = sorted(Draft7Validator(self.schema).iter_errors(_plain(data)), key=lambda issue: list(issue.absolute_path))
        if errors:
            issue = errors[0]
            path = ".".join(str(part) for part in issue.absolute_path) or "config"
            raise LlamaSwapSchemaError(f"{path}: {issue.message}")
