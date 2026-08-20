"""Regression tests against the current upstream llama-swap config format.

These pin the app's read/write paths to a representative upstream-shaped
configuration (tests/data/upstream_example_minimal.yaml, trimmed from the
upstream config.example.yaml at the snapshot commit in
app.services.llama_swap_service): every model-level field the current
upstream ModelConfig supports, preservation of unknown model-level fields,
groups+matrix routing coexistence (the current upstream rule), rejection of
malformed values without touching the file, and the provenance of the
bundled schema snapshot.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from ruamel.yaml import YAML

from app.services.llama_swap_service import (
    LlamaSwapError,
    LlamaSwapService,
    SNAPSHOT_DATE,
    UPSTREAM_REPOSITORY,
    UPSTREAM_SCHEMA_COMMIT,
    UPSTREAM_SCHEMA_PATH,
    UPSTREAM_SCHEMA_SHA256,
)
from app.widgets.llama_swap_advanced import GeneralSettingsEditor, RoutingEditor

FIXTURE = Path(__file__).parent / "data" / "upstream_example_minimal.yaml"
SCHEMA = Path(__file__).parents[1] / "data" / "llama_swap_config_schema.json"

app = QApplication.instance() or QApplication([])


def make_service(tmp_path: Path) -> LlamaSwapService:
    config = tmp_path / "config.yaml"
    config.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return LlamaSwapService(config, 3)


def load_yaml(path: Path):
    instance = YAML(typ="rt")
    instance.preserve_quotes = True
    return instance.load(Path(path).read_text(encoding="utf-8"))


def edit(editor_class, service, mutate=None):
    editor = editor_class()
    editor.load(service.load(), service)
    if mutate:
        mutate(editor)
    editor.apply(service)
    return editor


def test_fixture_is_current_upstream_shape(tmp_path):
    service = make_service(tmp_path)
    data = service.load()  # full validation: schema + semantic layer
    model = data["models"]["alpha-model"]
    for key in (
        "macros", "cmd", "name", "env", "proxy", "checkEndpoint", "ttl",
        "unloadTimeout", "cmdStop", "compat", "useModelName", "filters",
        "metadata", "timeouts", "concurrencyLimit", "aliases",
    ):
        assert key in model, f"fixture lost model field {key!r}"
    assert data["logRequests"] is True
    settings = data["routing"]["router"]["settings"]
    assert "groups" in settings and "matrix" in settings


def test_model_write_preserves_all_current_fields_and_unknowns(tmp_path):
    service = make_service(tmp_path)
    before = load_yaml(service.path)
    with service.transaction("models") as data:
        data["models"]["alpha-model"]["ttl"] = 120
    after = load_yaml(service.path)
    assert after["models"]["alpha-model"]["ttl"] == 120
    for key, value in before["models"]["alpha-model"].items():
        if key != "ttl":
            assert after["models"]["alpha-model"][key] == value
    # An unknown model-level field must survive round trips.
    assert after["models"]["alpha-model"]["modelNotes"].startswith("unknown field")
    # Unrelated sections must survive.
    assert after["macros"] == {"llama-bin": "llama-server"}
    assert after["routing"]["router"]["settings"]["matrix"]["sets"] == {"small": "a & b"}


def test_update_model_metadata_preserves_command_fields(tmp_path):
    service = make_service(tmp_path)
    service.update_model_metadata("alpha-model", {"description": "Updated"})
    model = load_yaml(service.path)["models"]["alpha-model"]
    assert model["description"] == "Updated"
    assert model["cmdStop"] == "docker stop ${MODEL_ID}"
    assert model["env"] == ["CUDA_VISIBLE_DEVICES=0"]
    assert model["proxy"] == "http://127.0.0.1:${PORT}"
    assert model["timeouts"] == {"connect": 30, "idleConn": 90}
    assert model["aliases"] == ["alpha"]


@pytest.mark.parametrize(
    "key, value",
    [
        ("cmdStop", ["not", "a", "string"]),
        ("env", "CUDA_VISIBLE_DEVICES=0"),
        ("macros", "not-a-mapping"),
        ("metadata", "not-a-mapping"),
        ("compat", True),
        ("concurrencyLimit", "4"),
        ("proxy", 8080),
        ("ttl", -5),
        ("timeouts", {"bogusKey": 1}),
        ("timeouts", {"connect": "soon"}),
    ],
)
def test_invalid_model_field_rejected_file_untouched(tmp_path, key, value):
    service = make_service(tmp_path)
    original = service.path.read_text(encoding="utf-8")
    with pytest.raises(LlamaSwapError):
        with service.transaction("models") as data:
            data["models"]["alpha-model"][key] = value
    assert service.path.read_text(encoding="utf-8") == original


def test_matrix_and_groups_coexistence_survives_group_mode_save(tmp_path):
    """Current upstream: both engines may sit under settings; `use` picks the
    active one. Saving in group mode must not destroy the inactive matrix."""
    service = make_service(tmp_path)
    edit(RoutingEditor, service)  # no-op save; fixture is group-active
    settings = load_yaml(service.path)["routing"]["router"]["settings"]
    assert "groups" in settings
    assert "matrix" in settings
    assert settings["matrix"]["vars"] == {"a": "alpha-model", "b": "beta-model"}
    assert settings["groups"]["swapgroup"]["members"] == ["alpha-model", "beta-model"]


def test_matrix_engine_requires_matrix_settings(tmp_path):
    text = (
        "models:\n"
        "  m1:\n"
        "    cmd: llama-server -m one.gguf --port ${PORT}\n"
        "routing:\n"
        "  router:\n"
        "    use: matrix\n"
    )
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    service = LlamaSwapService(path, 3)
    with pytest.raises(LlamaSwapError, match="settings.matrix"):
        with service.transaction("routing"):
            pass
    assert path.read_text(encoding="utf-8") == text


def test_scheduler_priority_accepts_alias_rejects_unknown(tmp_path):
    service = make_service(tmp_path)
    # The fixture's fifo priority references the alias "alpha" and passes
    # (see test_fixture_is_current_upstream_shape). An unknown target fails.
    original = service.path.read_text(encoding="utf-8")
    with pytest.raises(LlamaSwapError, match="priority"):
        with service.transaction("routing") as data:
            data["routing"]["scheduler"]["settings"]["fifo"]["priority"]["ghost"] = 9
    assert service.path.read_text(encoding="utf-8") == original


def test_duplicate_group_member_rejected(tmp_path):
    service = make_service(tmp_path)
    with pytest.raises(LlamaSwapError, match="Duplicate"):
        with service.transaction("routing") as data:
            data["routing"]["router"]["settings"]["groups"]["swapgroup"]["members"].append("beta-model")


def test_general_editor_log_requests_roundtrip_preserves_globals(tmp_path):
    service = make_service(tmp_path)
    before = load_yaml(service.path)
    edit(
        GeneralSettingsEditor,
        service,
        lambda e: e.log_requests.choice.setCurrentIndex(2),  # explicit false
    )
    after = load_yaml(service.path)
    assert after["logRequests"] is False
    for key, value in before.items():
        if key != "logRequests":
            assert after[key] == value


def test_bundled_schema_snapshot_provenance():
    """The bundled schema artifact must match the declared upstream snapshot.

    UPSTREAM_SCHEMA_SHA256 is the SHA-256 of the RAW upstream
    config-schema.json bytes at the pinned commit, stored byte-for-byte in
    data/llama_swap_config_schema.pristine.json. The bundled
    data/llama_swap_config_schema.json must equal that pristine schema
    modulo the top-level "x-source" provenance annotation (compared at the
    JSON parse level, so whitespace and key order are irrelevant).
    """
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    provenance = schema.get("x-source", {})
    assert provenance.get("repository") == UPSTREAM_REPOSITORY
    assert provenance.get("upstream_path") == UPSTREAM_SCHEMA_PATH
    assert provenance.get("upstream_commit") == UPSTREAM_SCHEMA_COMMIT
    assert provenance.get("snapshot_date") == SNAPSHOT_DATE
    pristine_path = SCHEMA.parent / "llama_swap_config_schema.pristine.json"
    assert hashlib.sha256(pristine_path.read_bytes()).hexdigest() == UPSTREAM_SCHEMA_SHA256
    pristine = json.loads(pristine_path.read_text(encoding="utf-8"))
    bundled_body = {key: value for key, value in schema.items() if key != "x-source"}
    assert bundled_body == pristine
