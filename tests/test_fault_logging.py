"""The telemetry → fault → job-note path, offscreen."""

from __future__ import annotations

import pytest

from benchhud.config import Config
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.jobs.store import JobStore
from benchhud.shell.main_window import MainWindow


@pytest.fixture
def store() -> JobStore:
    s = JobStore(":memory:")
    yield s
    s.close()


def _armed_window(qtbot, store, *, threshold=1.0, auto_log=True) -> MainWindow:
    config = Config()
    config.faults.auto_log = auto_log
    config.faults.current_threshold_a = threshold
    window = MainWindow(config, job_store=store)
    qtbot.addWidget(window)
    return window


def _notes(store, job_id):
    return [n.text for n in store.list_notes(job_id)]


def test_over_current_logs_a_note_to_the_selected_job(qtbot, store) -> None:
    job = store.create_job("shorted board")
    window = _armed_window(qtbot, store)
    window.jobs_panel.select_job(job.id)

    window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=0.2))  # normal
    window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=3.1))  # short

    notes = _notes(store, job.id)
    assert len(notes) == 1
    assert "Over-current fault" in notes[0]
    assert "3.10 A" in notes[0]


def test_sustained_short_logs_once(qtbot, store) -> None:
    job = store.create_job("board")
    window = _armed_window(qtbot, store)
    window.jobs_panel.select_job(job.id)

    for _ in range(5):
        window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=3.0))

    assert len(_notes(store, job.id)) == 1


def test_no_logging_when_disabled(qtbot, store) -> None:
    job = store.create_job("board")
    window = _armed_window(qtbot, store, auto_log=False)
    window.jobs_panel.select_job(job.id)

    window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=9.0))

    assert _notes(store, job.id) == []


def test_no_logging_without_a_selected_job(qtbot, store) -> None:
    store.create_job("board")  # exists but not selected
    window = _armed_window(qtbot, store)
    window.jobs_panel.job_list.clearSelection()
    window.jobs_panel.job_list.setCurrentItem(None)

    # Must not raise even with nothing selected.
    window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=9.0))


def test_missing_current_is_ignored(qtbot, store) -> None:
    job = store.create_job("board")
    window = _armed_window(qtbot, store)
    window.jobs_panel.select_job(job.id)

    window.on_telemetry(TelemetrySnapshot(voltage=5.0))  # DMM-only, no PSU current

    assert _notes(store, job.id) == []


def test_reconfigure_applies_a_new_threshold(qtbot, store) -> None:
    job = store.create_job("board")
    window = _armed_window(qtbot, store, threshold=10.0)
    window.jobs_panel.select_job(job.id)

    window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=3.0))  # below 10 A
    assert _notes(store, job.id) == []

    window._config.faults.current_threshold_a = 1.0
    window.reconfigure_faults()
    window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=3.0))  # now above 1 A

    assert len(_notes(store, job.id)) == 1
