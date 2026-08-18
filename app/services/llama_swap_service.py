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
        return data

    def models(self) -> CommentedMap:
        return self.load()["models"]

    def add_model(self, model_id: str, command: str, display_name: str = "") -> None:
        data = self.load()
        models = data["models"]
        if model_id in models:
            raise DuplicateModelError(f"Model ID already exists: {model_id}")
        entry = CommentedMap()
        if display_name:
            entry["name"] = display_name
        entry["cmd"] = command
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

    def _safe_write(self, data: CommentedMap) -> None:
        # Serialize and validate before touching the existing file.
        temporary = NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=self.path.parent, suffix=".tmp")
        temporary_path = Path(temporary.name)
        try:
            self._validate_data(data)
            with temporary:
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
