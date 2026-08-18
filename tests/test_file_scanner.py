from app.services.file_scanner import scan_gguf_models


def test_scanner_uses_first_standard_multishard_loader_entry(tmp_path):
    for name in ("Model-00001-of-00003.gguf", "Model-00002-of-00003.gguf", "Model-00003-of-00003.gguf", "Other.gguf"):
        (tmp_path / name).touch()
    assert [path.name for path in scan_gguf_models(str(tmp_path))] == ["Model-00001-of-00003.gguf", "Other.gguf"]
