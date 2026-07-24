"""Tests for the Jobs panel (in-memory store, offscreen)."""

from __future__ import annotations

import pytest

from benchhud.jobs.store import JobStatus, JobStore
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.jobs import JobsPanel


@pytest.fixture
def store() -> JobStore:
    s = JobStore(":memory:")
    yield s
    s.close()


def test_metadata(qtbot, store) -> None:
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    assert panel.panel_id == "jobs"
    assert panel.objectName() == "panel__jobs"


def test_lists_existing_jobs_newest_first(qtbot, store) -> None:
    store.create_job("board A")
    store.create_job("board B")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    assert panel.job_list.count() == 2
    assert "board B" in panel.job_list.item(0).text()


def test_create_job_from_device_box(qtbot, store) -> None:
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.new_device_edit.setText("MacBook A1706")
    panel.create_job()

    assert panel.job_list.count() == 1
    assert panel.new_device_edit.text() == ""  # cleared
    assert panel.selected_job_id() is not None  # newly created job is selected
    assert [j.device for j in store.list_jobs()] == ["MacBook A1706"]


def test_create_ignores_empty_device(qtbot, store) -> None:
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.new_device_edit.setText("   ")
    panel.create_job()
    assert panel.job_list.count() == 0


def test_selecting_a_job_loads_detail(qtbot, store) -> None:
    job = store.create_job("board", serial="SN1", customer="Acme", symptoms="no power")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.select_job(job.id)

    assert panel.device_edit.text() == "board"
    assert panel.serial_edit.text() == "SN1"
    assert panel.customer_edit.text() == "Acme"
    assert panel.symptoms_edit.toPlainText() == "no power"
    assert panel.status_combo.currentText() == JobStatus.OPEN


def test_save_writes_detail_back(qtbot, store) -> None:
    job = store.create_job("board")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.select_job(job.id)

    panel.serial_edit.setText("SN-42")
    panel.symptoms_edit.setPlainText("shorted rail")
    panel.status_combo.setCurrentText(JobStatus.IN_PROGRESS)
    panel.save_current()

    saved = store.get_job(job.id)
    assert saved.serial == "SN-42"
    assert saved.symptoms == "shorted rail"
    assert saved.status == JobStatus.IN_PROGRESS


def test_add_note_appends_to_timeline(qtbot, store) -> None:
    job = store.create_job("board")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.select_job(job.id)

    panel.note_edit.setText("measured 0.5 ohm")
    panel.add_note()

    assert panel.notes_list.count() == 1
    assert "measured 0.5 ohm" in panel.notes_list.item(0).text()
    assert panel.note_edit.text() == ""
    assert [n.text for n in store.list_notes(job.id)] == ["measured 0.5 ohm"]


def test_add_note_ignored_without_selection_or_text(qtbot, store) -> None:
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.note_edit.setText("orphan note")
    panel.add_note()  # nothing selected
    assert panel.notes_list.count() == 0

    job = store.create_job("board")
    panel.refresh_jobs()
    panel.select_job(job.id)
    panel.note_edit.setText("   ")
    panel.add_note()  # empty text
    assert store.list_notes(job.id) == []


def test_shell_hosts_jobs_panel(qtbot, store) -> None:
    window = MainWindow(job_store=store)
    qtbot.addWidget(window)
    assert "jobs" in window.panels.panel_ids()


def test_note_timestamp_is_shortened(qtbot, store) -> None:
    from benchhud.shell.panels.jobs import _short_stamp

    assert _short_stamp("2026-07-22T03:37:53+00:00") == "2026-07-22 03:37"


def test_write_report_creates_a_markdown_file(qtbot, store, tmp_path) -> None:
    job = store.create_job("board X", symptoms="no boot")
    store.add_note(job.id, "checked rails")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)

    out = tmp_path / "report.md"
    assert panel.write_report(job.id, str(out)) is True

    text = out.read_text(encoding="utf-8")
    assert "# Repair Report" in text
    assert "board X" in text
    assert "checked rails" in text


def test_write_report_missing_job_returns_false(qtbot, store, tmp_path) -> None:
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    out = tmp_path / "nope.md"

    assert panel.write_report(999, str(out)) is False
    assert not out.exists()


def test_export_button_present(qtbot, store) -> None:
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    assert panel.export_button.text() == "Export report…"


def test_slug_is_filename_safe() -> None:
    from benchhud.shell.panels.jobs import _slug

    assert _slug("iPhone 12 / logic board") == "iphone-12-logic-board"
    assert _slug("") == "job"
