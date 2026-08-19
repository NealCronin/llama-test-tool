import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QListWidgetItem
from ruamel.yaml import YAML

from app.settings import AppSettings
from app.services.llama_swap_service import LlamaSwapError, LlamaSwapService
from app.widgets.config_viewer import ConfigViewer
from app.widgets.llama_swap_advanced import (
    ActivityPerformanceEditor,
    GeneralSettingsEditor,
    HooksEditor,
    LoggingSettingsEditor,
    MacrosEditor,
    PeersEditor,
    ProfilesEditor,
    RoutingEditor,
    SecurityEditor,
    SelectorsEditor,
    UpstreamEditor,
)

BASE = """# llama-swap config
models:
  llama-model:
    ttl: 300
    cmd: llama-server --port ${PORT} -m model.gguf
  other-model:
    cmd: llama-server --port ${PORT} -m other.gguf
logLevel: debug
apiKeys:
  - "secret-1"
store:
  path: /data/store
performance:
  every: 30s
  disabled: false
unknownFuture:
  nested: keep-me
"""

app = QApplication.instance() or QApplication([])


def make_service(tmp_path: Path, text: str = BASE) -> LlamaSwapService:
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    return LlamaSwapService(config, 3)


def load_yaml(path: Path):
    instance = YAML(typ="rt")
    instance.preserve_quotes = True
    return instance.load(Path(path).read_text(encoding="utf-8"))


def set_optional(widget, value, explicit: bool = True) -> None:
    widget.set_explicit.setChecked(explicit)
    if explicit:
        widget.edit.setText(str(value))


def edit(editor_class, service, mutate=None):
    editor = editor_class()
    editor.load(service.load(), service)
    if mutate:
        mutate(editor)
    editor.apply(service)
    return editor


ALL_EDITORS = (
    GeneralSettingsEditor,
    LoggingSettingsEditor,
    ActivityPerformanceEditor,
    SecurityEditor,
    MacrosEditor,
    HooksEditor,
    UpstreamEditor,
    ProfilesEditor,
    SelectorsEditor,
    RoutingEditor,
    PeersEditor,
)


def test_every_editor_noop_is_stable_and_semantically_identical(tmp_path):
    service = make_service(tmp_path)
    original = load_yaml(service.path)
    for editor_class in ALL_EDITORS:
        service.path.write_text(BASE, encoding="utf-8")
        editor = editor_class()
        editor.load(service.load(), service)
        editor.apply(service)
        once = service.path.read_text(encoding="utf-8")
        assert load_yaml(service.path) == original, f"{editor_class.__name__} changed the configuration on a no-op save"
        # A second no-op save must not touch the file again (idempotent writes).
        editor = editor_class()
        editor.load(service.load(), service)
        editor.apply(service)
        assert service.path.read_text(encoding="utf-8") == once, f"{editor_class.__name__} is not stable across repeated no-op saves"


def test_noop_does_not_create_absent_sections(tmp_path):
    service = make_service(tmp_path)
    for editor_class in (ProfilesEditor, SelectorsEditor, PeersEditor, RoutingEditor, MacrosEditor, HooksEditor, UpstreamEditor):
        service.path.write_text(BASE, encoding="utf-8")
        edit(editor_class, service)
    data = load_yaml(service.path)
    for key in ("profiles", "selectors", "peers", "routing", "macros", "hooks", "upstream", "ui"):
        assert key not in data, f"no-op save created section {key!r}"
    assert data["unknownFuture"] == {"nested": "keep-me"}


def test_routing_priority_roundtrip(tmp_path):
    service = make_service(tmp_path)
    edit(RoutingEditor, service, lambda e: e.priority.set_items([("llama-model", "5"), ("other-model", "3")]))
    data = load_yaml(service.path)
    priority = data["routing"]["scheduler"]["settings"]["fifo"]["priority"]
    assert priority == {"llama-model": 5, "other-model": 3}
    assert data["models"]["llama-model"]["ttl"] == 300
    assert data["unknownFuture"] == {"nested": "keep-me"}

def test_routing_priority_unknown_model_rejected(tmp_path):
    service = make_service(tmp_path)
    editor = RoutingEditor()
    editor.load(service.load(), service)
    editor.priority.set_items([("ghost-model", "5")])
    with pytest.raises(LlamaSwapError, match="unknown model"):
        editor.apply(service)
    assert "routing" not in load_yaml(service.path)


def test_routing_group_unknown_member_rejected(tmp_path):
    service = make_service(tmp_path)
    editor = RoutingEditor()
    editor.load(service.load(), service)
    editor.router_use.setCurrentIndex(editor.router_use.findData("group"))
    editor.groups_list.addItem(QListWidgetItem("one"))
    editor.groups_list.setCurrentItem(editor.groups_list.item(0))
    editor.group_members.set_values(["ghost-model"])
    with pytest.raises(LlamaSwapError, match="unknown model"):
        editor.apply(service)
    assert "routing" not in load_yaml(service.path)


def test_routing_legacy_and_router_conflict_rejected(tmp_path):
    text = BASE + "groups:\n  - group: one\n    members: [llama-model]\n"
    service = make_service(tmp_path, text)
    editor = RoutingEditor()
    editor.load(service.load(), service)
    editor.router_use.setCurrentIndex(editor.router_use.findData("group"))
    editor.groups_list.addItem(QListWidgetItem("one"))
    editor.groups_list.setCurrentItem(editor.groups_list.item(0))
    editor.group_members.set_values(["llama-model"])
    with pytest.raises(LlamaSwapError, match="Legacy"):
        editor.apply(service)
    assert service.path.read_text(encoding="utf-8") == text


def test_routing_group_roundtrip_preserves_other_groups(tmp_path):
    text = BASE + "routing:\n  router:\n    use: group\n    settings:\n      groups:\n        one:\n          swap: true\n          members: [llama-model]\n        two:\n          exclusive: true\n          members: [other-model]\n          persistent: true\n"
    service = make_service(tmp_path, text)
    editor = RoutingEditor()
    editor.load(service.load(), service)
    editor.groups_list.setCurrentRow(0)
    editor.group_persistent.choice.setCurrentIndex(editor.group_persistent.choice.findData(True))
    editor.apply(service)
    groups = load_yaml(service.path)["routing"]["router"]["settings"]["groups"]
    assert groups["one"]["persistent"] is True
    assert groups["one"]["members"] == ["llama-model"]
    assert groups["two"]["exclusive"] is True
    assert groups["two"]["members"] == ["other-model"]
    assert groups["two"]["persistent"] is True


def test_routing_reset_removes_section(tmp_path):
    text = BASE + "routing:\n  scheduler:\n    use: fifo\n    settings:\n      fifo:\n        priority:\n          llama-model: 2\n"
    service = make_service(tmp_path, text)
    editor = RoutingEditor()
    editor.load(service.load(), service)
    editor.reset(service)
    assert "routing" not in load_yaml(service.path)


def test_activity_store_path_save_and_reset(tmp_path):
    service = make_service(tmp_path)
    edit(ActivityPerformanceEditor, service, lambda e: set_optional(e.store_path, "/new/store"))
    assert load_yaml(service.path)["store"]["path"] == "/new/store"
    edit(ActivityPerformanceEditor, service, lambda e: set_optional(e.store_path, "", explicit=False))
    assert "store" not in load_yaml(service.path)
    assert load_yaml(service.path)["performance"] == {"every": "30s", "disabled": False}


def test_ui_session_headers_presence(tmp_path):
    service = make_service(tmp_path)
    edit(ActivityPerformanceEditor, service, lambda e: (
        e.ui_configured.setChecked(True),
        e.session_id.set_values(["X-Custom-Session"]),
    ))
    assert load_yaml(service.path)["ui"]["activity"]["session_id"] == ["X-Custom-Session"]
    edit(ActivityPerformanceEditor, service, lambda e: e.ui_configured.setChecked(False))
    assert "ui" not in load_yaml(service.path)


def test_apikeys_roundtrip_and_reset(tmp_path):
    service = make_service(tmp_path)
    edit(SecurityEditor, service, lambda e: e.keys.set_values(["${env.KEY_A}", "literal-2"]))
    assert load_yaml(service.path)["apiKeys"] == ["${env.KEY_A}", "literal-2"]
    edit(SecurityEditor, service, lambda e: e.keys.set_values([]))
    assert "apiKeys" not in load_yaml(service.path)


def test_macros_and_hooks_roundtrip(tmp_path):
    service = make_service(tmp_path)
    edit(MacrosEditor, service, lambda e: e._add_row("model", "model.gguf", "string"))
    edit(HooksEditor, service, lambda e: e.preload.set_values(["llama-model"]))
    data = load_yaml(service.path)
    assert data["macros"]["model"] == "model.gguf"
    assert data["hooks"]["on_startup"]["preload"] == ["llama-model"]
    edit(MacrosEditor, service, lambda e: e.rows.clear())
    edit(HooksEditor, service, lambda e: e.preload.set_values([]))
    data = load_yaml(service.path)
    assert "macros" not in data
    assert "hooks" not in data


def test_hooks_preload_unknown_model_rejected(tmp_path):
    service = make_service(tmp_path)
    editor = HooksEditor()
    editor.load(service.load(), service)
    editor.preload.set_values(["ghost-model"])
    with pytest.raises(LlamaSwapError, match="unknown model"):
        editor.apply(service)
    assert "hooks" not in load_yaml(service.path)


def test_upstream_roundtrip(tmp_path):
    service = make_service(tmp_path)
    edit(UpstreamEditor, service, lambda e: e.ignore_paths.set_values([r"^/v1/completions$"]))
    assert load_yaml(service.path)["upstream"]["ignorePaths"] == [r"^/v1/completions$"]
    edit(UpstreamEditor, service, lambda e: e.ignore_paths.set_values([]))
    assert "upstream" not in load_yaml(service.path)


def test_profiles_roundtrip(tmp_path):
    service = make_service(tmp_path)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor.list.addItem("fast")
    editor.list.setCurrentRow(0)
    editor.description.setText("Quick model")
    editor.pins.set_items([("llama-model", "other-model")])
    editor.apply(service)
    profile = load_yaml(service.path)["profiles"]["fast"]
    assert profile["description"] == "Quick model"
    assert profile["pins"] == {"llama-model": "other-model"}
    assert "other-model" in load_yaml(service.path)["models"]


def test_selectors_spillover_roundtrip(tmp_path):
    service = make_service(tmp_path)
    editor = SelectorsEditor()
    editor.load(service.load(), service)
    editor.list.addItem("vision")
    editor.list.setCurrentRow(0)
    editor.name.setText("Vision models")
    editor.targets.set_values(["llama-model"])
    editor.strategy.setCurrentText("spillover")
    set_optional(editor.spillover, 3)
    editor.apply(service)
    entry = load_yaml(service.path)["selectors"]["vision"]
    assert entry["name"] == "Vision models"
    assert entry["targets"] == ["llama-model"]
    assert entry["strategy"] == "spillover"
    assert entry["settings"]["spillover"] == 3
    assert "other-model" in load_yaml(service.path)["models"]


def test_peers_roundtrip(tmp_path):
    service = make_service(tmp_path)
    editor = PeersEditor()
    editor.load(service.load(), service)
    editor.list.addItem("remote-a")
    editor.list.setCurrentRow(0)
    editor.proxy.setText("http://peer-a:5800")
    editor.models.set_values(["llama-model"])
    editor.api_key.setText("${env.PEER_KEY}")
    set_optional(editor.timeouts["connect"], 60)
    editor.apply(service)
    peer = load_yaml(service.path)["peers"]["remote-a"]
    assert peer["proxy"] == "http://peer-a:5800"
    assert peer["models"] == ["llama-model"]
    assert peer["apiKey"] == "${env.PEER_KEY}"
    assert peer["timeouts"]["connect"] == 60
    assert load_yaml(service.path)["models"]["other-model"]["cmd"].endswith("other.gguf")


def test_general_fields_save_and_reset(tmp_path):
    service = make_service(tmp_path)
    edit(GeneralSettingsEditor, service, lambda e: (
        set_optional(e.health_check, 60),
        e.send_loading.choice.setCurrentIndex(e.send_loading.choice.findData(True)),
    ))
    data = load_yaml(service.path)
    assert data["healthCheckTimeout"] == 60
    assert data["sendLoadingState"] is True
    assert "logLevel" in data
    edit(GeneralSettingsEditor, service, lambda e: (
        set_optional(e.health_check, "", explicit=False),
        e.send_loading.choice.setCurrentIndex(0),
    ))
    data = load_yaml(service.path)
    assert "healthCheckTimeout" not in data
    assert "sendLoadingState" not in data


def make_viewer(tmp_path: Path, text: str = BASE) -> ConfigViewer:
    config = tmp_path / "config.yaml"
    config.write_text(text, encoding="utf-8")
    return ConfigViewer(AppSettings(llama_swap_config=str(config)))


def test_model_inherit_ttl_removes_key(tmp_path):
    viewer = make_viewer(tmp_path)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.ttl.explicit() == 300  # BASE stores ttl: 300
    viewer.ttl.reset()  # unset -> inherit global default
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert "ttl" not in entry
    assert entry["cmd"].endswith("model.gguf")


def test_model_custom_ttl_and_unload_saved(tmp_path):
    viewer = make_viewer(tmp_path)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    viewer.ttl.load(True, 600)
    viewer.unload.load(True, 30)
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert entry["ttl"] == 600
    assert entry["unloadTimeout"] == 30


def test_model_capabilities_expanded(tmp_path):
    viewer = make_viewer(tmp_path)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    viewer.caps_configured.setChecked(True)
    viewer.caps_in["text"].setChecked(True)
    viewer.caps_out["text"].setChecked(True)
    viewer.caps_out["audio"].setChecked(True)
    viewer.caps_context.set_explicit.setChecked(True)
    viewer.caps_context.edit.setText("8192")
    viewer._save_model_settings()
    capabilities = load_yaml(viewer.service().path)["models"]["llama-model"]["capabilities"]
    assert capabilities["in"] == ["text"]
    assert capabilities["out"] == ["text", "audio"]
    assert capabilities["context"] == 8192


def test_model_settings_noop_preserves_entry(tmp_path):
    viewer = make_viewer(tmp_path)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert entry == {"ttl": 300, "cmd": "llama-server --port ${PORT} -m model.gguf"}


# ---------------------------------------------------------------------------
# Optional model string fields (useModelName / checkEndpoint)
# ---------------------------------------------------------------------------

MODEL_FIELDS_TEXT = BASE.replace(
    "    ttl: 300\n",
    "    ttl: 300\n    useModelName: some-name\n    checkEndpoint: /health\n",
    1,
)


def test_model_optional_string_fields_load_and_noop_preserves(tmp_path):
    viewer = make_viewer(tmp_path, MODEL_FIELDS_TEXT)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.use_model_name.text() == "some-name"
    assert viewer.check_endpoint.text() == "/health"
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert entry["useModelName"] == "some-name"
    assert entry["checkEndpoint"] == "/health"


def test_model_optional_string_fields_edit_and_clear(tmp_path):
    viewer = make_viewer(tmp_path, MODEL_FIELDS_TEXT)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    viewer.use_model_name.setText("renamed-model")
    viewer.check_endpoint.setText("/custom/health")
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert entry["useModelName"] == "renamed-model"
    assert entry["checkEndpoint"] == "/custom/health"
    # Blank removes the key again.
    viewer.use_model_name.setText("")
    viewer.check_endpoint.setText("")
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert "useModelName" not in entry
    assert "checkEndpoint" not in entry


def test_model_legacy_non_string_optional_field_rejected_not_rewritten(tmp_path):
    config = tmp_path / "config.yaml"
    text = BASE.replace("    ttl: 300\n", "    ttl: 300\n    useModelName: 42\n", 1)
    config.write_text(text, encoding="utf-8")
    service = LlamaSwapService(config, 3)
    with pytest.raises(LlamaSwapError, match="useModelName"):
        service.load()
    assert config.read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# Multi-item drafts (edit A, switch B, edit B, back to A)
# ---------------------------------------------------------------------------

PROFILES_TEXT = BASE + """
profiles:
  coder:
    description: coder profile
    pins:
      llama-model: llama-model
  vision:
    description: vision profile
    pins:
      other-model: other-model
"""

SELECTORS_TEXT = BASE + """
selectors:
  fast:
    strategy: warm
    targets: [llama-model]
  slow:
    strategy: spillover
    targets: [other-model]
    settings:
      spillover: 2
"""

PEERS_TEXT = BASE + """
peers:
  alpha:
    proxy: http://alpha:5900
    models: [llama-model]
  beta:
    proxy: http://beta:5900
    models: [other-model]
"""

ROUTING_GROUPS_TEXT = BASE + """
routing:
  router:
    use: group
    settings:
      groups:
        one:
          swap: true
          members: [llama-model]
        two:
          exclusive: true
          members: [other-model]
"""


def test_profiles_drafts_survive_selection_switching(tmp_path):
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor.list.setCurrentRow(0)
    editor.description.setText("coder edited")
    editor.list.setCurrentRow(1)
    assert editor.description.text() == "vision profile"
    editor.description.setText("vision edited")
    editor.list.setCurrentRow(0)
    assert editor.description.text() == "coder edited"  # A's draft survived the round trip
    editor.apply(service)
    data = load_yaml(service.path)
    assert data["profiles"]["coder"]["description"] == "coder edited"
    assert data["profiles"]["vision"]["description"] == "vision edited"
    assert data["unknownFuture"] == {"nested": "keep-me"}
    assert data["models"]["other-model"]["cmd"].endswith("other.gguf")


def test_selectors_drafts_survive_selection_switching(tmp_path):
    service = make_service(tmp_path, SELECTORS_TEXT)
    editor = SelectorsEditor()
    editor.load(service.load(), service)
    editor.list.setCurrentRow(0)
    editor.name.setText("fast renamed")
    editor.list.setCurrentRow(1)
    editor.name.setText("slow renamed")
    editor.list.setCurrentRow(0)
    assert editor.name.text() == "fast renamed"
    editor.apply(service)
    data = load_yaml(service.path)
    assert data["selectors"]["fast"]["name"] == "fast renamed"
    assert data["selectors"]["slow"]["name"] == "slow renamed"
    assert data["selectors"]["slow"]["settings"]["spillover"] == 2


def test_peers_drafts_survive_selection_switching(tmp_path):
    service = make_service(tmp_path, PEERS_TEXT)
    editor = PeersEditor()
    editor.load(service.load(), service)
    editor.list.setCurrentRow(0)
    editor.proxy.setText("http://alpha:6000")
    editor.list.setCurrentRow(1)
    editor.proxy.setText("http://beta:6000")
    editor.list.setCurrentRow(0)
    assert editor.proxy.text() == "http://alpha:6000"
    editor.apply(service)
    data = load_yaml(service.path)
    assert data["peers"]["alpha"]["proxy"] == "http://alpha:6000"
    assert data["peers"]["beta"]["proxy"] == "http://beta:6000"


def test_routing_group_drafts_survive_selection_switching(tmp_path):
    service = make_service(tmp_path, ROUTING_GROUPS_TEXT)
    editor = RoutingEditor()
    editor.load(service.load(), service)
    assert editor.router_use.currentData() == "group"
    editor.groups_list.setCurrentRow(0)
    editor.group_members.set_values(["other-model"])
    editor.groups_list.setCurrentRow(1)
    editor.group_members.set_values(["llama-model"])
    editor.groups_list.setCurrentRow(0)
    assert editor.group_members.values() == ["other-model"]
    editor.apply(service)
    groups = load_yaml(service.path)["routing"]["router"]["settings"]["groups"]
    assert groups["one"]["members"] == ["other-model"]
    assert groups["two"]["members"] == ["llama-model"]
    assert groups["two"]["exclusive"] is True


def test_profiles_delete_removes_only_that_item(tmp_path):
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor.list.setCurrentRow(0)
    editor._remove_profile()
    editor.apply(service)
    data = load_yaml(service.path)
    assert list(data["profiles"]) == ["vision"]
    assert data["profiles"]["vision"] == {"description": "vision profile", "pins": {"other-model": "other-model"}}


def test_profiles_add_new_item_leaves_existing_untouched(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda parent, title, label: ("my profile", True))
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor._add_profile()
    assert [editor.list.item(i).text() for i in range(editor.list.count())] == ["coder", "vision", "my profile"]
    editor.description.setText("new profile")
    editor.pins.set_items([("llama-model", "llama-model")])
    editor.apply(service)
    data = load_yaml(service.path)
    assert data["profiles"]["coder"] == {"description": "coder profile", "pins": {"llama-model": "llama-model"}}
    assert data["profiles"]["vision"] == {"description": "vision profile", "pins": {"other-model": "other-model"}}
    assert data["profiles"]["my profile"] == {"description": "new profile", "pins": {"llama-model": "llama-model"}}


# ---------------------------------------------------------------------------
# Profile pins: blank target = disabled (null), blank source = invalid
# ---------------------------------------------------------------------------


def test_profile_blank_pin_target_saves_null(tmp_path):
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor.list.setCurrentRow(1)
    editor.pins.set_items([("vision", "")])
    editor.apply(service)
    pins = load_yaml(service.path)["profiles"]["vision"]["pins"]
    assert pins["vision"] is None


def test_profile_blank_pin_source_rejected(tmp_path):
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor.list.setCurrentRow(1)
    editor.pins.set_items([("", "foo")])
    with pytest.raises(ValueError, match="without a source"):
        editor.apply(service)
    assert load_yaml(service.path)["profiles"]["vision"]["pins"] == {"other-model": "other-model"}


def test_profile_without_pins_rejected(tmp_path):
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor.list.setCurrentRow(1)
    editor.pins.set_items([])
    with pytest.raises(ValueError, match="at least one pin"):
        editor.apply(service)
def test_profile_add_path_rejects_empty_id(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text: shown.append(text)))
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    monkeypatch.setattr(QInputDialog, "getText", lambda parent, title, label: ("   ", True))
    editor._add_profile()
    assert editor.list.count() == 2
    assert shown and "empty" in shown[0]

def test_profile_id_accepts_any_nonempty_string(tmp_path):
    service = make_service(tmp_path, PROFILES_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor._validate_new_profile_id("fast")
    editor._validate_new_profile_id("my profile")  # spaces are schema-valid
    editor._validate_new_profile_id("Weird-Id_9")
    with pytest.raises(ValueError, match="empty"):
        editor._validate_new_profile_id("   ")
    with pytest.raises(ValueError, match="already exists"):
        editor._validate_new_profile_id("coder")


# ---------------------------------------------------------------------------
# YAML preservation around multi-item saves
# ---------------------------------------------------------------------------

PROFILES_COMMENT_TEXT = """# llama-swap config
models:
  llama-model:
    cmd: llama-server --port ${PORT} -m model.gguf
    unknownField: preserved
profiles:
  # keep this comment
  coder:
    description: coder profile  # trailing comment
    pins:
      llama-model: llama-model
"""


def test_profiles_noop_save_preserves_comments_and_unknown_fields(tmp_path):
    service = make_service(tmp_path, PROFILES_COMMENT_TEXT)
    editor = ProfilesEditor()
    editor.load(service.load(), service)
    editor.apply(service)
    text = service.path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "description: coder profile  # trailing comment" in text
    data = load_yaml(service.path)
    assert data["models"]["llama-model"]["unknownField"] == "preserved"


# ---------------------------------------------------------------------------
# TTL / unloadTimeout presence semantics (P1)
# ---------------------------------------------------------------------------


def test_ttl_absent_loads_unset_and_noop_keeps_absent(tmp_path):
    text = "# llama-swap config\nmodels:\n  llama-model:\n    cmd: llama-server -m model.gguf\n"
    viewer = make_viewer(tmp_path, text)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.ttl.explicit() is None
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert "ttl" not in entry
    assert "unloadTimeout" not in entry


def test_ttl_minus_one_explicit_survives_noop(tmp_path):
    text = BASE.replace("    ttl: 300\n", "    ttl: -1\n", 1)
    viewer = make_viewer(tmp_path, text)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.ttl.explicit() == -1
    viewer._save_model_settings()
    assert load_yaml(viewer.service().path)["models"]["llama-model"]["ttl"] == -1


def test_ttl_600_explicit_survives_noop(tmp_path):
    text = BASE.replace("    ttl: 300\n", "    ttl: 600\n", 1)
    viewer = make_viewer(tmp_path, text)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.ttl.explicit() == 600
    viewer._save_model_settings()
    assert load_yaml(viewer.service().path)["models"]["llama-model"]["ttl"] == 600


def test_journey_model_timeout_state_roundtrip(tmp_path):
    # Load explicit 600/30, unset both, save, reload: keys must be absent and
    # a second no-op save must leave them absent.
    text = BASE.replace("    ttl: 300\n", "    ttl: 600\n    unloadTimeout: 30\n", 1)
    viewer = make_viewer(tmp_path, text)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.ttl.explicit() == 600
    assert viewer.unload.explicit() == 30
    viewer.ttl.reset()  # unset -> inherit global
    viewer.unload.reset()
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert "ttl" not in entry
    assert "unloadTimeout" not in entry
    viewer.refresh()
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.ttl.explicit() is None
    assert viewer.unload.explicit() is None
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert "ttl" not in entry and "unloadTimeout" not in entry


def test_explicit_ttl_minus_one_and_unload_zero_survive_noop(tmp_path):
    text = BASE.replace("    ttl: 300\n", "    ttl: -1\n    unloadTimeout: 0\n", 1)
    viewer = make_viewer(tmp_path, text)
    viewer.list.setCurrentRow(0)
    viewer._select(viewer.list.item(0))
    assert viewer.ttl.explicit() == -1
    assert viewer.unload.explicit() == 0
    viewer._save_model_settings()
    entry = load_yaml(viewer.service().path)["models"]["llama-model"]
    assert entry["ttl"] == -1
    assert entry["unloadTimeout"] == 0
