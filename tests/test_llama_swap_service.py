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
