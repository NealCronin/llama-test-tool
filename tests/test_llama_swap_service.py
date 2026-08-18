from pathlib import Path

import pytest

from app.services.llama_swap_service import LlamaSwapError, LlamaSwapService


ORIGINAL = """ttl: 600
aliases:
  shared: old-model
models:
  old-model:
    ttl: 300
    aliases:
      - old
    cmd: llama-server --port ${PORT} -m old.gguf
"""


def service(tmp_path: Path) -> tuple[Path, LlamaSwapService]:
    path = tmp_path / "config.yaml"
    path.write_text(ORIGINAL, encoding="utf-8")
    return path, LlamaSwapService(path)


def test_add_preserves_existing_models_and_top_level_configuration(tmp_path):
    path, swap = service(tmp_path)
    swap.add_model("new-model", "llama-server --port ${PORT} -m new.gguf", "New model")
    data = swap.load()
    assert data["ttl"] == 600
    assert data["models"]["old-model"]["ttl"] == 300
    assert data["models"]["old-model"]["aliases"] == ["old"]
    assert data["models"]["new-model"]["cmd"].endswith("new.gguf")
    assert list(tmp_path.glob("config.yaml.bak-*"))


def test_replace_preserves_other_model_properties(tmp_path):
    _, swap = service(tmp_path)
    swap.replace_command("old-model", "llama-server --port ${PORT} -m replacement.gguf")
    old = swap.load()["models"]["old-model"]
    assert old["ttl"] == 300
    assert old["aliases"] == ["old"]
    assert old["cmd"].endswith("replacement.gguf")


def test_remove_preserves_other_models_and_top_level_configuration(tmp_path):
    _, swap = service(tmp_path)
    swap.add_model("new-model", "llama-server --port ${PORT} -m new.gguf")
    swap.remove_model("new-model")
    data = swap.load()
    assert "new-model" not in data["models"]
    assert "old-model" in data["models"]
    assert data["aliases"]["shared"] == "old-model"


def test_malformed_yaml_is_never_overwritten(tmp_path):
    path = tmp_path / "bad.yaml"
    original = "models: [not-a-mapping\n"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(LlamaSwapError):
        LlamaSwapService(path).add_model("new", "llama-server -m new.gguf")
    assert path.read_text(encoding="utf-8") == original


def test_global_edits_and_model_metadata_preserve_unknown_configuration(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("""healthCheckTimeout: 600
logLevel: info
logToStdout: both
includeAliasesInList: false
globalTTL: 0
unloadTimeout: 30
futureSetting: preserved
models:
  old:
    cmd: custom-server -m old.gguf
    futureModelField: preserved
""", encoding="utf-8")
    swap = LlamaSwapService(path)
    swap.update_globals({"globalTTL": 120, "logLevel": "warn"})
    swap.update_model_metadata("old", {"ttl": -1, "unloadTimeout": 0, "description": "Old model", "capabilities": {"context": 8192}})
    data = swap.load()
    assert data["futureSetting"] == "preserved"
    assert data["models"]["old"]["futureModelField"] == "preserved"
    assert data["models"]["old"]["ttl"] == -1
    assert data["models"]["old"]["unloadTimeout"] == 0


def test_invalid_managed_global_field_refuses_write(tmp_path):
    path, swap = service(tmp_path)
    original = path.read_text(encoding="utf-8")
    with pytest.raises(LlamaSwapError, match="logLevel"):
        swap.update_globals({"logLevel": "verbose"})
    assert path.read_text(encoding="utf-8") == original


def test_current_capability_keys_validate_and_legacy_keys_refuse_write(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("models:\n  qwen:\n    cmd: llama-server -m qwen.gguf\n", encoding="utf-8")
    swap = LlamaSwapService(path)
    swap.update_model_metadata("qwen", {"capabilities": {"in": ["text", "image"], "out": ["text"], "tools": True, "reranker": False, "context": 131072}})
    assert swap.load()["models"]["qwen"]["capabilities"]["in"] == ["text", "image"]
    with pytest.raises(LlamaSwapError, match="unknown key"):
        swap.update_model_metadata("qwen", {"capabilities": {"input": ["text"]}})


def test_inherit_timeouts_removes_model_overrides(tmp_path):
    path, swap = service(tmp_path)
    count = swap.inherit_model_timeouts()
    entry = swap.load()["models"]["old-model"]
    assert count == 1
    assert "ttl" not in entry and "unloadTimeout" not in entry


def test_realistic_config_global_noop_does_not_inject_start_port_or_change_models(tmp_path):
    path = tmp_path / "config.yaml"
    original = """# retained comment
healthCheckTimeout: 600
logLevel: info
logToStdout: both
includeAliasesInList: false
globalTTL: 0
unloadTimeout: 30
futureField: keep
models:
  qwen:
    cmd: |
      Engines/llama.cpp/build-mixed/bin/Release/llama-server.exe
      --port
      ${PORT}
      -m
      Models/qwen.gguf
    ttl: 0
    unloadTimeout: 30
    capabilities:
      in:
        - text
      out:
        - text
      tools: true
      context: 131072
"""
    path.write_text(original, encoding="utf-8")
    swap = LlamaSwapService(path)
    swap.update_globals({})
    data = swap.load()
    assert "startPort" not in data
    assert data["models"]["qwen"]["capabilities"]["in"] == ["text"]
    assert data["futureField"] == "keep"


def test_realistic_config_changes_only_requested_global(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("healthCheckTimeout: 600\nglobalTTL: 0\nmodels:\n  qwen:\n    cmd: llama-server -m qwen.gguf\n", encoding="utf-8")
    swap = LlamaSwapService(path)
    swap.update_globals({"globalTTL": 60})
    data = swap.load()
    assert data["globalTTL"] == 60
    assert data["models"]["qwen"]["cmd"] == "llama-server -m qwen.gguf"
