"""Offscreen user-journey tests for the Hugging Face download tab.

Drives the real HfCliService (QProcess) against the same hermetic fake ``hf``
batch CLI used by the service tests, so the journeys exercise widget
composition, command construction, dry-run previews, queue reorder, and
cancellation the way a user would — not through private helper calls alone.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

QApplication.instance() or QApplication([])

from app.models.hf_download import HfJobState, HfRepoType, HfSelectionMode, HfTarget
from app.services.hf_cli_service import HfCliService
from app.settings import AppSettings
from app.widgets.hf_download_tab import HfDownloadTab

# Shared hermetic-fake helpers live next to the service tests; importing the
# module keeps one fake-CLI contract instead of two drifting copies.
from test_hf_cli_service import SENTINEL, _shutdown_services, _spin_until, _write_fake_hf, fake_hf_path  # type: ignore[import-not-found]


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch, tmp_path) -> None:
    """Point AppSettings at a scratch file so journeys never touch user config."""
    monkeypatch.setattr(AppSettings, "path", classmethod(lambda cls: tmp_path / "settings.json"))


@pytest.fixture(autouse=True)
def _service_teardown(_shutdown_services) -> None:  # reuse the QProcess-safe teardown
    return None


@pytest.fixture
def launch_tab(fake_hf_path, tmp_path):
    _write_fake_hf(fake_hf_path)
    settings = AppSettings(
        models_folder=str(fake_hf_path / "models"),
        mmproj_folder=str(fake_hf_path / "mmproj"),
        drafters_folder=str(fake_hf_path / "drafters"),
        template_folder=str(fake_hf_path / "templates"),
        hf_destination=HfTarget.MODELS.value,
        hf_repo_type=HfRepoType.MODEL.value,
    )
    service = HfCliService()
    tab = HfDownloadTab(settings, service)
    tab.show()
    QApplication.processEvents()
    _spin_until(lambda: service.capabilities is not None and service.capabilities.path)
    yield tab, service
    tab.close()


# ---------------------------------------------------------------------------
# P0: the repository form must visibly exist and drive the request
# ---------------------------------------------------------------------------


def test_repo_form_widgets_are_visible_and_usable(launch_tab):
    tab, _service = launch_tab
    QApplication.processEvents()
    for widget in (tab.repo_edit, tab.revision_edit, tab.repo_type_combo, tab.files_edit, tab.exclude_edit):
        assert widget.isVisibleTo(tab), f"{widget} is not reachable in the visible hierarchy"
    # entering text must change the live command preview
    tab.repo_edit.setText("owner/repo")
    tab.revision_edit.setText("main")
    QApplication.processEvents()
    assert "owner/repo" in tab.command_preview.text()
    assert "--revision main" in tab.command_preview.text()


def test_journey_a_form_drives_queued_request(launch_tab):
    tab, service = launch_tab
    tab.repo_edit.setText("owner/repo")
    tab.revision_edit.setText("main")
    tab.exact_radio.setChecked(True)
    tab.files_edit.setText("model.gguf")
    request, error = tab._build_request()
    assert error == ""
    assert request is not None
    assert request.repo_id == "owner/repo"
    assert request.revision == "main"
    assert request.selection_mode is HfSelectionMode.EXACT
    assert request.filenames == ("model.gguf",)
    assert request.target is HfTarget.MODELS
    QApplication.processEvents()
    assert "owner/repo" in tab.command_preview.text()
    tab._add_to_queue()
    job = service.jobs()[-1]
    assert job.request.repo_id == "owner/repo"
    assert job.request.filenames == ("model.gguf",)
    assert job.request.revision == "main"


def test_journey_a_selectors_and_preview_agree_on_excludes(launch_tab):
    """ENTIRE + exclude is a legal request and visible in the preview."""
    tab, service = launch_tab
    tab.repo_edit.setText("owner/repo")
    QApplication.processEvents()
    tab.exclude_edit.setText("*.safetensors, *.bin")
    QApplication.processEvents()
    request, error = tab._build_request()
    assert error == ""
    assert request is not None
    assert request.selection_mode is HfSelectionMode.ENTIRE
    assert request.exclude == ("*.safetensors", "*.bin")
    assert "--exclude" in tab.command_preview.text()
    tab._add_to_queue()
    queued = service.jobs()[-1].request
    assert queued.exclude == ("*.safetensors", "*.bin")


# ---------------------------------------------------------------------------
# Journey B: repeated previews, no stale QProcess, no stale callbacks
# ---------------------------------------------------------------------------


def test_journey_b_preview_twice_no_deleted_object(launch_tab):
    tab, _service = launch_tab
    tab.repo_edit.setText("owner/repo")
    tab.exact_radio.setChecked(True)
    tab.files_edit.setText("model.gguf")
    tab._preview_download()
    _spin_until(lambda: "Total files:" in tab.preview_box.toPlainText())
    # The first preview's QProcess has been deleteLater'd by now; the button
    # must be re-enabled and a second run must not raise.
    assert tab.preview_button.isEnabled()
    tab._preview_download()
    _spin_until(lambda: tab.preview_box.toPlainText().count("model-Q4_K_M.gguf") >= 1)
    assert "Preview: 2/3 files" in tab.post_status.text()


def test_journey_b_stale_preview_cannot_overwrite_newer(launch_tab):
    service, _tab = None, None
    tab, _ = launch_tab
    tab.repo_edit.setText("o/slow")  # fake dry-run that sleeps 3s
    tab._preview_download()
    QApplication.processEvents()
    tab.repo_edit.setText("owner/repo")  # replace with a fast preview
    tab._preview_download()
    _spin_until(lambda: "Total files: 3" in tab.preview_box.toPlainText())
    # Give the killed slow preview's late callbacks every chance to land.
    _spin_until(lambda: tab.service._preview_process is None)
    time.sleep(0.7)
    QApplication.processEvents()
    text = tab.preview_box.toPlainText()
    assert "Total files: 3" in text
    assert "no dry-run output" not in text  # the stale preview's raw output must not win


def test_journey_b_preview_failure_is_surfaced(launch_tab):
    tab, _service = launch_tab
    tab.repo_edit.setText("o/dryfail")
    tab._preview_download()
    _spin_until(lambda: "Preview failed (exit code 3)" in tab.post_status.text())
    assert "Preview failed (exit code 3)" in tab.preview_box.toPlainText()
    assert "dry run failed" in tab.preview_box.toPlainText()  # raw output shown, redacted


# ---------------------------------------------------------------------------
# Journey C: reorder changes both the visible table and the execution order
# ---------------------------------------------------------------------------


def _table_job_ids(tab) -> list[str]:
    return [
        tab.queue_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in range(tab.queue_table.rowCount())
    ]


def _row_of(tab, job_id: str) -> int:
    for row in range(tab.queue_table.rowCount()):
        if tab.queue_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == job_id:
            return row
    return -1


def test_journey_c_reorder_moves_visible_and_execution_order(launch_tab):
    tab, service = launch_tab
    tab.repo_edit.setText("o/slow")  # A: slow download, stays active
    tab._add_to_queue()
    _spin_until(lambda: service.active_id is not None)
    tab.repo_edit.setText("o/r")
    tab._add_to_queue()  # B
    tab._add_to_queue()  # C
    a, b, c = _table_job_ids(tab)
    assert len((a, b, c)) == 3
    assert [job.id for job in service.jobs()] == [a, b, c]
    # Select C and move it up: visible table must change immediately.
    tab.queue_table.selectRow(_row_of(tab, c))
    tab.up_button.click()
    QApplication.processEvents()
    assert _table_job_ids(tab) == [a, c, b]
    assert [job.id for job in service.jobs()] == [a, c, b]
    # C is now at the head of the pending queue; a second Move Up leaves the
    # visible order unchanged (the active A always stays first, then C, then B).
    tab.queue_table.selectRow(_row_of(tab, c))
    tab.up_button.click()
    QApplication.processEvents()
    assert _table_job_ids(tab) == [a, c, b]
    assert [job.id for job in service.jobs()] == [a, c, b]
    # Order of execution after A must be C, then B.
    downloading: list[str] = []
    service.job_state_changed.connect(
        lambda job_id, state: downloading.append(job_id)
        if state == HfJobState.DOWNLOADING.value
        else None
    )
    tab.cancel_button.click()
    _spin_until(lambda: all(
        service.job(job_id) is not None and service.job(job_id).state
        in (HfJobState.COMPLETED, HfJobState.CANCELLED)
        for job_id in (a, b, c)
    ), timeout=60)
    assert downloading == [c, b]  # C runs first, then B


# ---------------------------------------------------------------------------
# Journey D: cancel-active leaves the pending queue untouched
# ---------------------------------------------------------------------------


def test_journey_d_cancel_active_leaves_queue_intact(launch_tab):
    tab, service = launch_tab
    tab.repo_edit.setText("o/slow")
    tab._add_to_queue()
    _spin_until(lambda: service.active_id is not None)
    tab.repo_edit.setText("o/r")
    tab._add_to_queue()
    tab._add_to_queue()
    a, b, c = _table_job_ids(tab)
    tab.cancel_button.click()
    _spin_until(lambda: service.job(a).state == HfJobState.CANCELLED, timeout=60)
    # B and C were not dropped: they run and complete.
    _spin_until(lambda: service.job(c).state == HfJobState.COMPLETED, timeout=60)
    _spin_until(lambda: service.job(b).state == HfJobState.COMPLETED, timeout=60)
    # The cancelled row stays visible in history; queued rows completed.
    statuses = {job_id: service.job(job_id).state.value for job_id in (a, b, c)}
    assert statuses[a] == "Cancelled"
    assert statuses[b] == "Completed"
    assert statuses[c] == "Completed"


# ---------------------------------------------------------------------------
# P2: an echo of the secret token never reaches any visible surface
# ---------------------------------------------------------------------------


def test_secret_never_reaches_visible_surfaces(launch_tab):
    tab, service = launch_tab
    tab.repo_edit.setText("o/leak")  # fake CLI echoes SENTINEL, exits 0
    tab._add_to_queue()
    _spin_until(lambda: service.jobs() and service.job(service.jobs()[-1].id).state == HfJobState.COMPLETED)
    public_text = "\n".join(
        [
            tab.console_box.toPlainText(),
            tab.post_status.text(),
            tab.command_preview.text(),
        ]
    )
    for row in range(tab.queue_table.rowCount()):
        for column in range(tab.queue_table.columnCount()):
            item = tab.queue_table.item(row, column)
            if item is not None:
                public_text += "\n" + item.text()
    assert SENTINEL not in public_text
    assert "hf_***" in public_text  # redaction visibly applied


# ---------------------------------------------------------------------------
# P2: post-download actions follow result semantics, not mere pathname diffs
# ---------------------------------------------------------------------------


def test_models_download_enables_open_copy_and_use_as_model(launch_tab):
    tab, service = launch_tab
    tab.repo_edit.setText("o/r")
    tab.exact_radio.setChecked(True)
    tab.files_edit.setText("fake-model.gguf")
    tab._add_to_queue()
    _spin_until(lambda: service.jobs() and service.job(service.jobs()[-1].id).state == HfJobState.COMPLETED)
    assert tab.open_folder_button.isEnabled()
    assert tab.copy_path_button.isEnabled()
    assert tab.use_as_buttons[HfTarget.MODELS].isEnabled()
    # exactly one candidate: the fake wrote exactly one .gguf
    assert not tab.use_as_buttons[HfTarget.MMProj].isEnabled()


def test_cache_download_has_no_local_destination_actions(launch_tab):
    tab, service = launch_tab
    tab.destination_combo.setCurrentIndex(tab.destination_combo.findData(HfTarget.CACHE.value))
    tab.repo_edit.setText("o/noop")  # fake echoes, exits 0, no --local-dir needed
    tab._add_to_queue()
    _spin_until(lambda: service.jobs() and service.job(service.jobs()[-1].id).state == HfJobState.COMPLETED)
    # Cache-only: no local folder to open, copy, or install as a model.
    assert not tab.open_folder_button.isEnabled()
    assert not tab.copy_path_button.isEnabled()
    assert not tab.use_as_buttons[HfTarget.MODELS].isEnabled()
    assert service.job(service.jobs()[-1].id).result.ok