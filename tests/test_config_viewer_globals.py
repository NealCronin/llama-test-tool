import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter
from ruamel.yaml import YAML

from app.settings import AppSettings
from app.widgets.config_viewer import ConfigViewer

CONFIG = "models:\n  old-model:\n    ttl: 300\n    cmd: llama-server --port ${PORT} -m old.gguf\n"

app = QApplication.instance() or QApplication([])


def yaml():
    instance = YAML(typ="rt")
    instance.preserve_quotes = True
    return instance


def make_viewer(tmp_path: Path) -> tuple[Path, ConfigViewer]:
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    settings = AppSettings(llama_swap_config=str(config))
    return config, ConfigViewer(settings)


def test_absent_log_to_stdout_displays_proxy_and_noop_save_does_not_inject(tmp_path):
    config, viewer = make_viewer(tmp_path)
    original = config.read_text(encoding="utf-8")
    editor = viewer.logging_editor
    assert editor.log_output.effective() == "proxy"
    assert editor.log_output.explicit() is None
    editor.apply(editor._service)
    viewer.general_editor.apply(viewer.general_editor._service)
    assert config.read_text(encoding="utf-8") == original
    data = yaml().load(config.read_text(encoding="utf-8"))
    assert "logToStdout" not in data
    assert "sendLoadingState" not in data
    assert "logLevel" not in data
    assert "healthCheckTimeout" not in data


def test_changing_log_to_stdout_writes_chosen_value(tmp_path):
    config, viewer = make_viewer(tmp_path)
    editor = viewer.logging_editor
    editor.log_output.choice.setCurrentIndex(editor.log_output.choice.findData("both"))
    editor.apply(editor._service)
    data = yaml().load(config.read_text(encoding="utf-8"))
    assert data["logToStdout"] == "both"
    viewer.refresh()
    assert viewer.logging_editor.log_output.explicit() == "both"


def test_explicit_send_loading_state_saves_true_and_false(tmp_path):
    config, viewer = make_viewer(tmp_path)
    editor = viewer.general_editor
    editor.send_loading.choice.setCurrentIndex(editor.send_loading.choice.findData(True))
    editor.apply(editor._service)
    assert yaml().load(config.read_text(encoding="utf-8"))["sendLoadingState"] is True
    viewer.refresh()
    editor = viewer.general_editor
    editor.send_loading.choice.setCurrentIndex(editor.send_loading.choice.findData(False))
    editor.apply(editor._service)
    assert yaml().load(config.read_text(encoding="utf-8"))["sendLoadingState"] is False
    viewer.refresh()
    assert viewer.general_editor.send_loading.explicit() is False  # explicit false, not collapsed to absent


def test_send_loading_state_absent_remains_absent_on_noop(tmp_path):
    config, viewer = make_viewer(tmp_path)
    assert "sendLoadingState" not in yaml().load(config.read_text(encoding="utf-8"))
    viewer.general_editor.apply(viewer.general_editor._service)
    assert "sendLoadingState" not in yaml().load(config.read_text(encoding="utf-8"))


def test_explicit_defaults_are_preserved_on_save(tmp_path):
    config, viewer = make_viewer(tmp_path)
    config.write_text("logLevel: debug\nmodels:\n  old-model:\n    cmd: llama-server -m old.gguf\n", encoding="utf-8")
    viewer.refresh()
    assert viewer.logging_editor.log_level.explicit() == "debug"
    viewer.logging_editor.apply(viewer.logging_editor._service)
    assert yaml().load(config.read_text(encoding="utf-8"))["logLevel"] == "debug"


def test_reset_to_default_removes_stored_keys(tmp_path):
    config, viewer = make_viewer(tmp_path)
    config.write_text(
        "logToStdout: both\nlogLevel: debug\napiKeys:\n  - secret-1\nmodels:\n  old-model:\n    cmd: llama-server -m old.gguf\n",
        encoding="utf-8",
    )
    viewer.refresh()
    viewer.logging_editor.reset(viewer.logging_editor._service)
    viewer.security_editor.reset(viewer.security_editor._service)
    data = yaml().load(config.read_text(encoding="utf-8"))
    assert "logToStdout" not in data
    assert "logLevel" not in data
    assert "apiKeys" not in data
    assert "old-model" in data["models"]


def test_absent_log_level_defaults_to_info_without_explicit(tmp_path):
    config, viewer = make_viewer(tmp_path)
    editor = viewer.logging_editor
    assert editor.log_level.effective() == "info"
    assert editor.log_level.explicit() is None
    editor.apply(editor._service)
    assert "logLevel" not in yaml().load(config.read_text(encoding="utf-8"))


def test_log_level_stored_equal_to_default_stays_explicit(tmp_path):
    config, viewer = make_viewer(tmp_path)
    config.write_text("logLevel: info\nmodels:\n  old-model:\n    cmd: llama-server -m old.gguf\n", encoding="utf-8")
    viewer.refresh()
    editor = viewer.logging_editor
    assert editor.log_level.effective() == "info"
    assert editor.log_level.explicit() == "info"
    editor.apply(editor._service)  # no-op save must not collapse the stored default
    assert yaml().load(config.read_text(encoding="utf-8"))["logLevel"] == "info"


def test_failed_validation_leaves_yaml_unchanged(tmp_path):
    config, viewer = make_viewer(tmp_path)
    original = config.read_text(encoding="utf-8")
    editor = viewer.general_editor
    editor.health_check.edit.setText("5")
    editor.health_check.set_explicit.setChecked(True)
    with pytest.raises(Exception, match="healthCheckTimeout"):
        editor.apply(editor._service)
    assert config.read_text(encoding="utf-8") == original


def test_ttl_placeholder_documents_upstream_ttl_semantics(tmp_path):
    _, viewer = make_viewer(tmp_path)
    placeholder = viewer.ttl.edit.placeholderText()
    assert placeholder == "unset = upstream default; -1 = global TTL; 0 = never unload; >0 = unload after N seconds"
    assert "-1 = global TTL" in placeholder
    assert "0 = never unload" in placeholder
    assert ">0 = unload after N seconds" in placeholder
    assert "-1 = never" not in placeholder


def test_models_right_pane_is_scrollable(tmp_path):
    _, viewer = make_viewer(tmp_path)
    viewer.list.itemClicked.emit(viewer.list.item(0))
    assert viewer.current_id == "old-model"
    models_page = viewer.tabs.widget(0)
    splitter = models_page.findChild(QSplitter)
    assert splitter is not None
    assert splitter.count() == 2
    assert splitter.widget(1) is viewer.model_scroll
    assert isinstance(viewer.model_scroll, QScrollArea)
    assert viewer.model_scroll.widgetResizable()
    assert not isinstance(splitter.widget(0), QScrollArea)  # left pane stays fixed

    def under(widget, ancestor) -> bool:
        while (widget := widget.parentWidget()) is not None:
            if widget is ancestor:
                return True
        return False

    content = viewer.model_scroll.widget()
    assert content is not None
    for widget in (viewer.raw, viewer.ttl, viewer.caps_context):
        assert under(widget, content)
    assert under(viewer.list, splitter.widget(0))
    viewer.close()
