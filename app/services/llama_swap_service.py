from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


class LlamaSwapError(RuntimeError):
    pass


class DuplicateModelError(LlamaSwapError):
    pass


GLOBAL_FIELDS = {
    "healthCheckTimeout", "globalTTL", "unloadTimeout", "logLevel", "logToStdout", "includeAliasesInList", "startPort",
}
_LOG_LEVELS = {"debug", "info", "warn", "error"}
_LOG_OUTPUTS = {"proxy", "upstream", "both", "none"}

class LlamaSwapService:
    """Round-trip, backup-first edits to a llama-swap configuration file."""

    def __init__(self, path: str | Path, backup_limit: int = 10) -> None:
        self.path = Path(path)
        self.backup_limit = max(1, backup_limit)
        self.yaml = YAML(typ="rt")
        self.yaml.preserve_quotes = True
        self.yaml.width = 120

    def load(self) -> CommentedMap:
        if not self.path.is_file():
            raise LlamaSwapError(f"llama-swap config is unavailable: {self.path}")
        try:
            data = self.yaml.load(self.path.read_text(encoding="utf-8"))
        except Exception as error:
            raise LlamaSwapError(f"Cannot parse llama-swap YAML: {error}") from error
        return self._validate_data(data)

    @staticmethod
    def _validate_data(data: object) -> CommentedMap:
        if data is None:
            data = CommentedMap()
        if not isinstance(data, CommentedMap):
            raise LlamaSwapError("llama-swap configuration must be a YAML mapping.")
        models = data.get("models")
        if models is None:
            data["models"] = CommentedMap()
            return data
        if not isinstance(models, CommentedMap):
            raise LlamaSwapError("The top-level models field must be a mapping.")
        for model_id, entry in models.items():
            if not isinstance(entry, CommentedMap) or not isinstance(entry.get("cmd"), str) or not entry["cmd"].strip():
                raise LlamaSwapError(f"Model {model_id!r} must be a mapping with a non-empty cmd string.")
            LlamaSwapService._validate_model_fields(model_id, entry)
        LlamaSwapService._validate_global_fields(data)
        return data

    def models(self) -> CommentedMap:
        return self.load()["models"]

    def add_model(self, model_id: str, command: str, display_name: str = "", metadata: dict[str, object] | None = None) -> None:
        data = self.load()
        models = data["models"]
        if model_id in models:
            raise DuplicateModelError(f"Model ID already exists: {model_id}")
        entry = CommentedMap()
        if display_name:
            entry["name"] = display_name
        for key, value in (metadata or {}).items():
            if value is not None:
                entry[key] = value
        entry["cmd"] = command
        self._validate_model_fields(model_id, entry)
        models[model_id] = entry
        self._safe_write(data)

    def replace_command(self, model_id: str, command: str) -> None:
        data = self.load()
        models = data["models"]
        if model_id not in models:
            raise LlamaSwapError(f"Model ID does not exist: {model_id}")
        if not isinstance(models[model_id], CommentedMap):
            raise LlamaSwapError(f"Model {model_id!r} is not a mapping.")
        models[model_id]["cmd"] = command
        self._safe_write(data)

    def duplicate(self, source_id: str, target_id: str) -> None:
        data = self.load()
        models = data["models"]
        if target_id in models:
            raise DuplicateModelError(f"Model ID already exists: {target_id}")
        if source_id not in models:
            raise LlamaSwapError(f"Model ID does not exist: {source_id}")
        from copy import deepcopy
        models[target_id] = deepcopy(models[source_id])
        self._safe_write(data)

    def remove_model(self, model_id: str) -> None:
        data = self.load()
        models = data["models"]
        if model_id not in models:
            raise LlamaSwapError(f"Model ID does not exist: {model_id}")
        del models[model_id]
        self._safe_write(data)


    def update_globals(self, values: dict[str, object]) -> None:
        data = self.load()
        for key, value in values.items():
            if key not in GLOBAL_FIELDS:
                raise LlamaSwapError(f"{key} is not an application-managed global setting.")
            data[key] = value
        self._safe_write(data)

    def update_model_metadata(self, model_id: str, values: dict[str, object]) -> None:
        data = self.load()
        entry = data["models"].get(model_id)
        if not isinstance(entry, CommentedMap):
            raise LlamaSwapError(f"Model ID does not exist: {model_id}")
        for key, value in values.items():
            entry[key] = value
        self._validate_model_fields(model_id, entry)
        self._safe_write(data)

    def inherit_model_timeouts(self) -> int:
        data = self.load()
        for entry in data["models"].values():
            entry["ttl"] = -1
            entry["unloadTimeout"] = 0
        self._safe_write(data)
        return len(data["models"])

    @staticmethod
    def _validate_global_fields(data: CommentedMap) -> None:
        for key in ("healthCheckTimeout", "globalTTL", "unloadTimeout", "startPort"):
            if key in data and (not isinstance(data[key], int) or isinstance(data[key], bool) or data[key] < 0):
                raise LlamaSwapError(f"{key} must be a non-negative integer.")
        if "startPort" in data and data["startPort"] > 65535:
            raise LlamaSwapError("startPort must be at most 65535.")
        if "logLevel" in data and data["logLevel"] not in _LOG_LEVELS:
            raise LlamaSwapError("logLevel must be one of debug, info, warn, error.")
        if "logToStdout" in data and data["logToStdout"] not in _LOG_OUTPUTS:
            raise LlamaSwapError("logToStdout must be one of proxy, upstream, both, none.")
        if "includeAliasesInList" in data and not isinstance(data["includeAliasesInList"], bool):
            raise LlamaSwapError("includeAliasesInList must be true or false.")

    @staticmethod
    def _validate_model_fields(model_id: object, entry: CommentedMap) -> None:
        for key in ("ttl", "unloadTimeout"):
            if key in entry and (not isinstance(entry[key], int) or isinstance(entry[key], bool) or entry[key] < (-1 if key == "ttl" else 0)):
                raise LlamaSwapError(f"Model {model_id!r} {key} has an invalid value.")
        for key in ("name", "description", "useModelName", "checkEndpoint"):
            if key in entry and not isinstance(entry[key], str):
                raise LlamaSwapError(f"Model {model_id!r} {key} must be a string.")
        if "capabilities" in entry and not isinstance(entry["capabilities"], (CommentedMap, dict)):
            raise LlamaSwapError(f"Model {model_id!r} capabilities must be a mapping.")
    def _safe_write(self, data: CommentedMap) -> None:
        # Validate before opening a replacement file; this keeps failed writes
        # from touching either the existing config or a locked Windows tempfile.
        self._validate_data(data)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=self.path.parent, suffix=".tmp") as temporary:
                temporary_path = Path(temporary.name)
                self.yaml.dump(data, temporary)
            try:
                serialized = self.yaml.load(temporary_path.read_text(encoding="utf-8"))
                self._validate_data(serialized)
            except Exception as error:
                raise LlamaSwapError(f"Refusing to write invalid YAML: {error}") from error
            backup = self.path.with_name(f"{self.path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
            backup.write_bytes(self.path.read_bytes())
            os.replace(temporary_path, self.path)
            self._trim_backups()
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def _trim_backups(self) -> None:
        backups = sorted(self.path.parent.glob(f"{self.path.name}.bak-*"), key=lambda item: item.stat().st_mtime, reverse=True)
        for backup in backups[self.backup_limit:]:
            backup.unlink()


def suggested_model_id(model_path: str) -> str:
    stem = Path(model_path).stem.casefold()
    result = "".join(character if character.isalnum() else "-" for character in stem).strip("-")
    return result[:64] or "llama-model"
