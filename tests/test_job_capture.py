"""Tests for capture-to-job (synthetic frames — no hardware)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from benchhud.instruments.owon_spm8104 import DmmReading
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.jobs.capture import CaptureContext, capture_to_job, compose_image, summarize
from benchhud.jobs.store import JobStore


def _scope(w: int = 64, h: int = 48) -> np.ndarray:
    return np.full((h, w, 3), 40, dtype=np.uint8)


def _temps(w: int = 32, h: int = 24) -> np.ndarray:
    temps = np.full((h, w), 28.0, dtype=np.float32)
    temps[5, 5] = 84.2
    return temps


def _snapshot() -> TelemetrySnapshot:
    dmm = DmmReading(mode="VDC", value=12.05, status=None, ranging="Auto", raw="")
    return TelemetrySnapshot(dmm=dmm, voltage=12.05, current=0.42)


# --- summary ---------------------------------------------------------------


def test_summarize_includes_readings_and_hotspot() -> None:
    text = summarize(CaptureContext(temps=_temps(), snapshot=_snapshot()))
    assert "12.05 V" in text
    assert "0.42 A" in text
    assert "5.1 W" in text  # 12.05 * 0.42
    assert "DMM[VDC] 12.05" in text
    assert "84.2" in text


def test_summarize_empty_context() -> None:
    assert summarize(CaptureContext()) == "capture"


def test_summarize_error_snapshot_is_skipped() -> None:
    text = summarize(CaptureContext(snapshot=TelemetrySnapshot(error="disconnected")))
    assert text == "capture"


# --- image composition -----------------------------------------------------


def test_compose_scope_and_thermal_side_by_side() -> None:
    image = compose_image(CaptureContext(scope=_scope(), temps=_temps()))
    assert image is not None
    assert image.shape[0] == 48  # matched to the taller tile
    assert image.shape[1] > 64  # scope + thermal side by side
    assert image.shape[2] == 3


def test_compose_scope_only() -> None:
    image = compose_image(CaptureContext(scope=_scope()))
    assert image.shape == (48, 64, 3)


def test_compose_thermal_only() -> None:
    image = compose_image(CaptureContext(temps=_temps()))
    assert image is not None
    assert image.shape[2] == 3


def test_compose_nothing_returns_none() -> None:
    assert compose_image(CaptureContext()) is None


# --- capture into a job ----------------------------------------------------


def test_capture_writes_image_and_note(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    try:
        job = store.create_job("board")
        note = capture_to_job(
            store,
            job.id,
            CaptureContext(scope=_scope(), temps=_temps(), snapshot=_snapshot()),
            stamp=lambda: "20260719-120000",
        )
        assert note.attachment is not None
        written = Path(note.attachment)
        assert written.exists() and written.suffix == ".png"
        assert written.parent == tmp_path / "job_attachments" / str(job.id)
        # the note is on the job's timeline with the readings summary
        timeline = store.list_notes(job.id)
        assert len(timeline) == 1
        assert "12.05 V" in timeline[0].text
    finally:
        store.close()


def test_capture_without_frames_still_notes_readings(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    try:
        job = store.create_job("board")
        note = capture_to_job(store, job.id, CaptureContext(snapshot=_snapshot()))
        assert note.attachment is None  # nothing to draw
        assert "12.05 V" in note.text
    finally:
        store.close()


# --- panel + shell wiring --------------------------------------------------


@pytest.fixture
def store() -> JobStore:
    s = JobStore(":memory:")
    yield s
    s.close()


def test_jobs_panel_capture_button_appends_note(qtbot, store) -> None:
    from benchhud.shell.panels.jobs import JobsPanel

    context = CaptureContext(scope=_scope(), temps=_temps(), snapshot=_snapshot())
    panel = JobsPanel(store, capture_provider=lambda: context)
    qtbot.addWidget(panel)
    job = store.create_job("board")
    panel.refresh_jobs()
    panel.select_job(job.id)

    panel.capture_bench()

    notes = store.list_notes(job.id)
    assert len(notes) == 1
    assert "12.05 V" in notes[0].text


def test_capture_ignored_without_selection(qtbot, store) -> None:
    from benchhud.shell.panels.jobs import JobsPanel

    panel = JobsPanel(store, capture_provider=lambda: CaptureContext(scope=_scope()))
    qtbot.addWidget(panel)
    panel.capture_bench()  # nothing selected -> no-op
    assert store.list_jobs() == []


def test_shell_capture_context_pulls_from_panels(qtbot, store) -> None:
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(job_store=store)
    qtbot.addWidget(window)
    window.scope_panel.update_frame(_scope())
    window.thermal_panel.update_temps(_temps())
    window.power_panel.update_from_snapshot(_snapshot())

    context = window.capture_context()
    assert context.scope is not None
    assert context.temps is not None
    assert context.snapshot is not None
    assert "12.05 V" in summarize(context)
