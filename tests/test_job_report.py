"""Tests for repair-report rendering (pure — no clock, no filesystem)."""

from __future__ import annotations

from benchhud.jobs.report import export_report, render_report
from benchhud.jobs.store import Job, JobNote, JobStore

_GEN = "2026-07-23T12:00:00+00:00"


def _job(**over) -> Job:
    base = {
        "id": 1,
        "device": "iPhone 12 logic board",
        "serial": "ABC123",
        "customer": "Jane Doe",
        "symptoms": "No power. Suspect U2 tristar.",
        "status": "done",
        "created_at": "2026-07-20T09:30:00+00:00",
        "updated_at": "2026-07-22T16:05:00+00:00",
        "external_ref": None,
    }
    base.update(over)
    return Job(**base)


def _note(text: str, *, stamp: str = "2026-07-21T10:00:00+00:00", attachment=None) -> JobNote:
    return JobNote(id=1, job_id=1, created_at=stamp, text=text, attachment=attachment)


def test_report_has_header_and_details() -> None:
    md = render_report(_job(), [], generated_at=_GEN)
    assert md.startswith("# Repair Report")
    assert "| Device | iPhone 12 logic board |" in md
    assert "| Serial | ABC123 |" in md
    assert "| Customer | Jane Doe |" in md


def test_status_is_humanized() -> None:
    assert "| Status | Completed |" in render_report(_job(status="done"), [], generated_at=_GEN)
    assert "| Status | Not repairable |" in render_report(
        _job(status="scrapped"), [], generated_at=_GEN
    )


def test_unknown_status_passes_through() -> None:
    assert "| Status | weird |" in render_report(_job(status="weird"), [], generated_at=_GEN)


def test_timestamps_are_humanized() -> None:
    md = render_report(_job(), [], generated_at=_GEN)
    assert "2026-07-20 09:30 UTC" in md  # opened
    assert "2026-07-23 12:00 UTC" in md  # generated footer


def test_symptoms_section() -> None:
    md = render_report(_job(symptoms="Backlight dead"), [], generated_at=_GEN)
    assert "## Reported symptoms" in md
    assert "Backlight dead" in md


def test_empty_symptoms_placeholder() -> None:
    md = render_report(_job(symptoms=""), [], generated_at=_GEN)
    assert "_None recorded._" in md


def test_work_log_lists_notes_in_order() -> None:
    notes = [
        _note("Injected 3.3V, U2 pulled 800mA", stamp="2026-07-21T10:00:00+00:00"),
        _note("Replaced U2, board boots", stamp="2026-07-21T11:30:00+00:00"),
    ]
    md = render_report(_job(), notes, generated_at=_GEN)
    first = md.index("Injected 3.3V")
    second = md.index("Replaced U2")
    assert first < second
    assert "2026-07-21 10:00 UTC" in md


def test_empty_work_log_placeholder() -> None:
    assert "_No entries recorded._" in render_report(_job(), [], generated_at=_GEN)


def test_attachment_is_referenced_by_filename_only() -> None:
    note = _note("Thermal shows U2 hot", attachment="/home/x/job_attachments/1/2026.png")
    md = render_report(_job(), [note], generated_at=_GEN)
    assert "Capture: `2026.png`" in md
    assert "/home/x/" not in md  # no local path leaks into a customer document


def test_external_ref_shown_when_present() -> None:
    assert "| Reference | SHOP-42 |" in render_report(
        _job(external_ref="SHOP-42"), [], generated_at=_GEN
    )
    assert "Reference" not in render_report(_job(external_ref=None), [], generated_at=_GEN)


def test_pipe_in_field_is_escaped() -> None:
    md = render_report(_job(device="A|B board"), [], generated_at=_GEN)
    assert r"A\|B board" in md


def test_export_report_pulls_from_store() -> None:
    store = JobStore(":memory:", now=lambda: "2026-07-20T09:30:00+00:00")
    job = store.create_job(device="Test board", symptoms="dead")
    store.add_note(job.id, "did a thing")

    md = export_report(store, job.id, generated_at=_GEN)

    assert md is not None
    assert "Test board" in md
    assert "did a thing" in md
    store.close()


def test_export_report_missing_job_returns_none() -> None:
    store = JobStore(":memory:")
    assert export_report(store, 999, generated_at=_GEN) is None
    store.close()
