from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from app.services.llama_swap_validation import LlamaSwapConfigValidator, LlamaSwapSchemaError


class LlamaSwapError(RuntimeError):
    pass


class DuplicateModelError(LlamaSwapError):
    pass


GLOBAL_FIELDS = {
    "healthCheckTimeout", "globalTTL", "unloadTimeout", "logLevel", "logTimeFormat", "logToStdout",
    "includeAliasesInList", "startPort", "sendLoadingState",
}
_LOG_LEVELS = {"debug", "info", "warn", "error"}
_LOG_TIME_FORMATS = (
    "", "ansic", "unixdate", "rubydate", "rfc822", "rfc822z", "rfc850", "rfc1123", "rfc1123z",
    "rfc3339", "rfc3339nano", "kitchen", "stamp", "stampmilli", "stampmicro", "stampnano",
)
_LOG_OUTPUTS = {"proxy", "upstream", "both", "none"}
EDITABLE_SUBTREES = frozenset({"store", "performance", "ui", "macros", "hooks", "upstream", "profiles", "selectors", "routing", "peers", "apiKeys"})

def update_leaf(data: CommentedMap, path: Sequence[str], value: object | None) -> None:
    """Set or remove ``value`` at ``path`` inside a loaded configuration.

    Intermediate mappings are created as needed. On removal, mappings that end up
    empty are trimmed away again so resetting a leaf never leaves empty sections.
    Unknown fields at every level are untouched.
    """
    path = tuple(str(key) for key in path)
    if not path:
        raise LlamaSwapError("A non-empty path is required.")
    if path[0] not in EDITABLE_SUBTREES and path[0] != "models":
        raise LlamaSwapError(f"{path[0]!r} is not an editable llama-swap configuration section.")
    node: CommentedMap = data
    trail: list[tuple[CommentedMap, str]] = []
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, (CommentedMap, dict)):
            child = CommentedMap()
            node[key] = child
        trail.append((node, key))
        node = child
    if value is None:
        node.pop(path[-1], None)
        for parent, key in reversed(trail):
            current = parent.get(key)
            if not isinstance(current, (CommentedMap, dict)) or current:
                break
            parent.pop(key, None)
    else:
        node[path[-1]] = value


class LlamaSwapService:
    """Round-trip, backup-first edits to a llama-swap configuration file."""

    def __init__(self, path: str | Path, backup_limit: int = 10) -> None:
        self.path = Path(path)
        self.backup_limit = max(1, backup_limit)
        self.yaml = YAML(typ="rt")
        self.yaml.preserve_quotes = True
        self.yaml.width = 120
        self.validator = LlamaSwapConfigValidator()

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
        self._safe_write(data, "models")

    def replace_command(self, model_id: str, command: str) -> None:
        data = self.load()
        models = data["models"]
        if model_id not in models:
            raise LlamaSwapError(f"Model ID does not exist: {model_id}")
        if not isinstance(models[model_id], CommentedMap):
            raise LlamaSwapError(f"Model {model_id!r} is not a mapping.")
        models[model_id]["cmd"] = command
        self._safe_write(data, "models")

    def duplicate(self, source_id: str, target_id: str) -> None:
        data = self.load()
        models = data["models"]
        if target_id in models:
            raise DuplicateModelError(f"Model ID already exists: {target_id}")
        if source_id not in models:
            raise LlamaSwapError(f"Model ID does not exist: {source_id}")
        from copy import deepcopy
        models[target_id] = deepcopy(models[source_id])
        self._safe_write(data, "models")

    def remove_model(self, model_id: str) -> None:
        data = self.load()
        models = data["models"]
        if model_id not in models:
            raise LlamaSwapError(f"Model ID does not exist: {model_id}")
        del models[model_id]
        self._safe_write(data, "models")

    def apply_globals(self, values: dict[str, object], remove: Iterable[str] = ()) -> None:
        """Presence-aware global update: explicit values are written, named keys are removed."""
        data = self.load()
        for key in remove:
            if key not in GLOBAL_FIELDS:
                raise LlamaSwapError(f"{key} is not an application-managed global setting.")
            data.pop(key, None)
        for key, value in values.items():
            if key not in GLOBAL_FIELDS:
                raise LlamaSwapError(f"{key} is not an application-managed global setting.")
            data[key] = value
        self._safe_write(data, "globals")

    def update_globals(self, values: dict[str, object]) -> None:
        self.apply_globals(values)

    def update_subtree(self, name: str, value: object | None) -> None:
        if name not in EDITABLE_SUBTREES:
            raise LlamaSwapError(f"{name} is not an editable llama-swap configuration subtree.")
        data = self.load()
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
        self._safe_write(data, name)

    def update_nested(self, path: Sequence[str], value: object | None) -> None:
        """Set or remove a value at a nested path, preserving unknown fields at every level."""
        with self.transaction(path[0] if path else None) as data:
            update_leaf(data, path, value)

    @contextmanager
    def transaction(self, touched: str | None = None):
        """Load once, mutate in place, then validate and write exactly once.

        Any exception raised by the caller leaves the file untouched; the full
        configuration is schema- and semantics-validated before the atomic replace.
        """
        data = self.load()
        try:
            yield data
        except BaseException:
            raise
        self._safe_write(data, touched)

    def update_model_metadata(self, model_id: str, values: dict[str, object]) -> None:
        data = self.load()
        entry = data["models"].get(model_id)
        if not isinstance(entry, CommentedMap):
            raise LlamaSwapError(f"Model ID does not exist: {model_id}")
        for key, value in values.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        self._validate_model_fields(model_id, entry)
        self._safe_write(data, "models")

    def inherit_model_timeouts(self) -> int:
        data = self.load()
        for entry in data["models"].values():
            entry.pop("ttl", None)
            entry.pop("unloadTimeout", None)
        self._safe_write(data, "models")
        return len(data["models"])

    @staticmethod
    def _validate_global_fields(data: CommentedMap) -> None:
        if "healthCheckTimeout" in data and (not isinstance(data["healthCheckTimeout"], int) or isinstance(data["healthCheckTimeout"], bool) or data["healthCheckTimeout"] < 15):
            raise LlamaSwapError("healthCheckTimeout must be an integer of at least 15 seconds.")
        for key in ("globalTTL", "unloadTimeout", "metricsMaxInMemory", "captureBuffer"):
            if key in data and (not isinstance(data[key], int) or isinstance(data[key], bool) or data[key] < 0):
                raise LlamaSwapError(f"{key} must be a non-negative integer.")
        if "startPort" in data and (not isinstance(data["startPort"], int) or isinstance(data["startPort"], bool) or not 1 <= data["startPort"] <= 65535):
            raise LlamaSwapError("startPort must be a usable port from 1 to 65535.")
        if "logLevel" in data and data["logLevel"] not in _LOG_LEVELS:
            raise LlamaSwapError("logLevel must be one of debug, info, warn, error.")
        if "logToStdout" in data and data["logToStdout"] not in _LOG_OUTPUTS:
            raise LlamaSwapError("logToStdout must be one of proxy, upstream, both, none.")
        for key in ("includeAliasesInList", "sendLoadingState"):
            if key in data and not isinstance(data[key], bool):
                raise LlamaSwapError(f"{key} must be true or false.")
        if "logTimeFormat" in data and data["logTimeFormat"] not in _LOG_TIME_FORMATS:
            raise LlamaSwapError(f"logTimeFormat must be one of: {', '.join(repr(value) for value in _LOG_TIME_FORMATS)}.")

    @staticmethod
    def _validate_model_fields(model_id: object, entry: CommentedMap) -> None:
        for key in ("ttl", "unloadTimeout"):
            if key in entry and (not isinstance(entry[key], int) or isinstance(entry[key], bool) or entry[key] < (-1 if key == "ttl" else 0)):
                raise LlamaSwapError(f"Model {model_id!r} {key} has an invalid value.")
        if "concurrencyLimit" in entry and (not isinstance(entry["concurrencyLimit"], int) or isinstance(entry["concurrencyLimit"], bool) or entry["concurrencyLimit"] < 0):
            raise LlamaSwapError(f"Model {model_id!r} concurrencyLimit must be a non-negative integer.")
        for key in ("name", "description", "useModelName", "checkEndpoint"):
            if key in entry and not isinstance(entry[key], str):
                raise LlamaSwapError(f"Model {model_id!r} {key} must be a string.")
        for key in ("unlisted", "sendLoadingState"):
            if key in entry and not isinstance(entry[key], bool):
                raise LlamaSwapError(f"Model {model_id!r} {key} must be true or false.")
        if "aliases" in entry and (not isinstance(entry["aliases"], list) or any(not isinstance(alias, str) or not alias for alias in entry["aliases"])):
            raise LlamaSwapError(f"Model {model_id!r} aliases must be a list of non-empty strings.")
        if "capabilities" not in entry:
            return
        capabilities = entry["capabilities"]
        if not isinstance(capabilities, (CommentedMap, dict)):
            raise LlamaSwapError(f"Model {model_id!r} capabilities must be a mapping.")
        unknown = set(capabilities) - {"in", "out", "tools", "reranker", "context"}
        if unknown:
            raise LlamaSwapError(f"Model {model_id!r} capabilities has unknown key(s): {', '.join(sorted(unknown))}.")
        for key in ("in", "out"):
            if key in capabilities and (not isinstance(capabilities[key], list) or not capabilities[key] or any(value not in {"text", "audio", "image"} for value in capabilities[key])):
                raise LlamaSwapError(f"Model {model_id!r} capabilities.{key} must be a non-empty list of text, audio, or image.")
        for key in ("tools", "reranker"):
            if key in capabilities and not isinstance(capabilities[key], bool):
                raise LlamaSwapError(f"Model {model_id!r} capabilities.{key} must be true or false.")
        if "context" in capabilities and (not isinstance(capabilities["context"], int) or isinstance(capabilities["context"], bool) or capabilities["context"] < 0):
            raise LlamaSwapError(f"Model {model_id!r} capabilities.context must be a non-negative integer.")

    @staticmethod
    def _validate_semantics(data: CommentedMap, touched: str | None) -> None:
        """Runtime-semantics checks for the subtrees a write actually touches.

        Pre-existing conflicts elsewhere in the file never block unrelated writes;
        a failed check raises before any file is modified.
        """
        models = data.get("models") or {}
        if touched in (None, "models"):
            seen_aliases: dict[str, str] = {}
            for model_id in models:
                seen_aliases[str(model_id)] = str(model_id)
            for model_id, entry in models.items():
                for alias in entry.get("aliases", []) or []:
                    owner = seen_aliases.get(str(alias))
                    if owner is not None:
                        raise LlamaSwapError(f"Alias {alias!r} is already used by {owner!r}; aliases must be unique across all model IDs and aliases.")
                    seen_aliases[str(alias)] = str(model_id)
        if touched in (None, "hooks"):
            preload = (((data.get("hooks") or {}).get("on_startup") or {}).get("preload") or [])
            for model_id in preload:
                if model_id not in models:
                    raise LlamaSwapError(f"hooks.on_startup.preload references unknown model {model_id!r}.")
        if touched in (None, "routing"):
            routing = data.get("routing")
            if routing is None:
                return
            legacy = [name for name in ("groups", "matrix") if name in data]
            if legacy and "router" in routing:
                raise LlamaSwapError(
                    f"Legacy top-level {legacy[0]!r} and routing.router cannot be used together; "
                    "remove the legacy section or stop configuring routing.router."
                )
            scheduler = routing.get("scheduler") or {}
            priority = ((scheduler.get("settings") or {}).get("fifo") or {}).get("priority") or {}
            for model_id in priority:
                if model_id not in models:
                    raise LlamaSwapError(f"routing.scheduler settings.fifo.priority references unknown model {model_id!r}.")
            router = routing.get("router") or {}
            use = router.get("use")
            settings = router.get("settings") or {}
            if use == "group" and "matrix" in settings:
                raise LlamaSwapError("routing.router.use is 'group' but settings also configures a matrix.")
            if use == "matrix" and "groups" in settings:
                raise LlamaSwapError("routing.router.use is 'matrix' but settings also configures groups.")
            groups = settings.get("groups") or {}
            membership: dict[str, str] = {}
            for group_name, group in groups.items():
                for member in (group or {}).get("members", []) or []:
                    if member not in models:
                        raise LlamaSwapError(f"Routing group {group_name!r} references unknown model {member!r}.")
                    owner = membership.get(str(member))
                    if owner is not None:
                        raise LlamaSwapError(f"Model {member!r} is a member of both {owner!r} and {group_name!r}; a model can only belong to one group.")
                    membership[str(member)] = str(group_name)

    def _safe_write(self, data: CommentedMap, touched: str | None = None) -> None:
        # Validate before opening a replacement file; this keeps failed writes
        self._validate_semantics(data, touched)
        try:
            self.validator.validate(data)
        except LlamaSwapSchemaError as error:
            raise LlamaSwapError(f"Official llama-swap schema validation failed: {error}") from error
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
