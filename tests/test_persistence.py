from pathlib import Path

from app.models.command import Command, CommandArgument
from app.settings import AppSettings


def test_save_restore_and_clear_builder_state(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(AppSettings, "path", classmethod(lambda cls: path))
    settings = AppSettings(models_folder="models", llama_cpp_folder="llama.cpp", llama_server_selected="llama-server", llama_fit_params_executable="llama-fit-params")
    command = Command(arguments=[CommandArgument("-m", ["missing.gguf"], "model"), CommandArgument("-c", ["4096"])])
    settings.last_command = command.to_dict()
    settings.save()
    restored = AppSettings.load()
    restored_command = Command.from_dict(restored.last_command)
    assert restored.models_folder == "models"
    assert restored.llama_cpp_folder == "llama.cpp"
    assert restored.llama_server_selected == "llama-server"
    assert restored.llama_fit_params_executable == "llama-fit-params"
    assert restored_command.model_path() == Path("missing.gguf")
    assert not restored_command.model_path().exists()
    restored_command.reset()
    assert [argument.flag for argument in restored_command.arguments] == ["-m"]
