"""Known-good readings (#235): the store, the comparison, the panel, the report."""

from __future__ import annotations

import pytest

from benchhud.config import Config
from benchhud.instruments.owon_spm8104 import DmmReading
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.jobs.readings import Verdict, compare, mode_from_dmm
from benchhud.jobs.report import export_report
from benchhud.jobs.store import JobStore
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.readings import ReadingsPanel

BOARD = "820-00239"


def dmm(mode: str, value: float | None, status: str | None = None) -> DmmReading:
    return DmmReading(mode=mode, value=value, status=status, ranging="AUTO", raw="")


@pytest.fixture
def store():
    store = JobStore(":memory:")
    yield store
    store.close()


def good_board(store: JobStore, readings: dict[str, float | None]) -> int:
    """A known-good board's diode-mode readings (None means OL)."""
    job = store.create_job("MacBook Pro", board_id=BOARD).id
    for point, value in readings.items():
        store.add_reading(
            job,
            point,
            "diode",
            value,
            status=None if value is not None else "OL",
            unit="V",
            reference=True,
        )
    return job


# --- the store ----------------------------------------------------------------------


def test_points_are_normalized(store) -> None:
    job = store.create_job("x").id
    reading = store.add_reading(job, "  pp3v3_s5 ", "diode", 0.41)
    assert reading.point == "PP3V3_S5"
    with pytest.raises(ValueError):
        store.add_reading(job, "   ", "diode", 0.4)


def test_references_come_from_the_same_board_only(store) -> None:
    good_board(store, {"PP3V3_S5": 0.41})
    other = store.create_job("Other", board_id="820-01234").id
    store.add_reading(other, "PP3V3_S5", "diode", 0.9, reference=True)
    not_marked = store.create_job("Same board, not known-good", board_id=BOARD).id
    store.add_reading(not_marked, "PP3V3_S5", "diode", 0.001)
    refs = store.reference_readings(BOARD)
    assert [r.value for r in refs] == [0.41]


def test_a_job_is_not_its_own_reference(store) -> None:
    job = good_board(store, {"PP3V3_S5": 0.41})
    assert store.reference_readings(BOARD, exclude_job=job) == []


def test_marking_a_job_known_good(store) -> None:
    job = store.create_job("fixed", board_id=BOARD).id
    store.add_reading(job, "PP5V_S0", "diode", 0.38)
    store.set_readings_reference(job, True)
    assert [r.point for r in store.reference_readings(BOARD)] == ["PP5V_S0"]


def test_readings_go_with_their_job(store) -> None:
    job = good_board(store, {"PP3V3_S5": 0.41})
    store.delete_job(job)
    assert store.reference_readings(BOARD) == []


def test_an_older_database_gains_the_table(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "jobs.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL,"
        " serial TEXT NOT NULL DEFAULT '', customer TEXT NOT NULL DEFAULT '',"
        " symptoms TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    store = JobStore(path)
    job = store.create_job("old db").id
    assert store.add_reading(job, "TP1", "ohm", 120.0).value == 120.0
    store.close()


# --- the comparison ----------------------------------------------------------------


def test_a_short_reads_low(store) -> None:
    good_board(store, {"PP3V3_S5": 0.412})
    job = store.create_job("bad", board_id=BOARD).id
    store.add_reading(job, "PP3V3_S5", "diode", 0.002)
    [row] = compare(store.list_readings(job), store.reference_readings(BOARD, exclude_job=job))
    assert row.verdict is Verdict.LOW and row.flagged and row.reference == 0.412


def test_within_tolerance_is_ok(store) -> None:
    good_board(store, {"PP3V3_S5": 0.412})
    job = store.create_job("ok", board_id=BOARD).id
    store.add_reading(job, "PP3V3_S5", "diode", 0.43)  # within 10%
    [row] = compare(store.list_readings(job), store.reference_readings(BOARD, exclude_job=job))
    assert row.verdict is Verdict.OK


def test_the_floor_keeps_near_zero_readings_quiet(store) -> None:
    good_board(store, {"PPBUS_G3H": 0.005})
    job = store.create_job("ok", board_id=BOARD).id
    store.add_reading(job, "PPBUS_G3H", "diode", 0.015)  # 3x, but within 0.02 V
    [row] = compare(store.list_readings(job), store.reference_readings(BOARD, exclude_job=job))
    assert row.verdict is Verdict.OK


def test_open_against_a_good_reading_and_back(store) -> None:
    good_board(store, {"PP1V8": 0.55, "NC_PIN": None})
    job = store.create_job("x", board_id=BOARD).id
    store.add_reading(job, "PP1V8", "diode", None, status="OL")
    store.add_reading(job, "NC_PIN", "diode", 0.3)
    rows = {r.point: r for r in compare(store.list_readings(job), store.reference_readings(BOARD))}
    assert rows["PP1V8"].verdict is Verdict.OPEN
    assert rows["NC_PIN"].verdict is Verdict.LOW and rows["NC_PIN"].reference_open


def test_the_reference_is_the_median_and_the_latest_reading_counts(store) -> None:
    good_board(store, {"PP3V3": 0.40})
    good_board(store, {"PP3V3": 0.41})
    good_board(store, {"PP3V3": 0.90})  # one odd board doesn't drag it
    job = store.create_job("x", board_id=BOARD).id
    store.add_reading(job, "PP3V3", "diode", 0.001)
    store.add_reading(job, "PP3V3", "diode", 0.405)  # re-measured after a reflow
    [row] = compare(store.list_readings(job), store.reference_readings(BOARD))
    assert row.reference == 0.41 and row.reference_count == 3 and row.verdict is Verdict.OK


def test_unmeasured_points_are_a_checklist(store) -> None:
    good_board(store, {"PP5V": 0.38, "PP3V3": 0.41, "PP1V8": 0.55})
    job = store.create_job("x", board_id=BOARD).id
    store.add_reading(job, "PP3V3", "diode", 0.41)
    rows = compare(store.list_readings(job), store.reference_readings(BOARD))
    assert [(r.point, r.verdict) for r in rows] == [
        ("PP3V3", Verdict.OK),
        ("PP1V8", Verdict.TODO),
        ("PP5V", Verdict.TODO),
    ]


def test_modes_compare_separately(store) -> None:
    good_board(store, {"PP3V3": 0.41})
    job = store.create_job("x", board_id=BOARD).id
    store.add_reading(job, "PP3V3", "ohm", 2000.0, unit="Ω")
    rows = compare(store.list_readings(job), store.reference_readings(BOARD))
    assert {(r.mode, r.verdict) for r in rows} == {("ohm", Verdict.NEW), ("diode", Verdict.TODO)}


def test_dmm_modes_map() -> None:
    assert mode_from_dmm("DIODE") == "diode" and mode_from_dmm("res") == "ohm"
    assert mode_from_dmm("VDC") == "vdc" and mode_from_dmm("CAP") is None


# --- the panel ---------------------------------------------------------------------


def _panel(qtbot, store, job):
    panel = ReadingsPanel(store, lambda: job)
    qtbot.addWidget(panel)
    return panel


def test_record_from_the_meter_and_see_the_verdict(qtbot, store) -> None:
    good_board(store, {"PP3V3_S5": 0.412, "PP5V_S5": 0.38})
    job = store.create_job("bad", board_id=BOARD).id
    panel = _panel(qtbot, store, job)
    assert "2 known-good points" in panel.board_label.text()
    assert panel.table.rowCount() == 2  # the checklist, before anything's measured

    panel.update_meter(dmm("DIODE", 0.002))
    panel.point_edit.setText("pp3v3_s5")
    panel.record()

    [reading] = store.list_readings(job)
    assert (reading.point, reading.mode, reading.value) == ("PP3V3_S5", "diode", 0.002)
    assert "LOW" in panel.status.text()
    assert panel.table.item(0, 3).text() == "LOW" and panel.point_edit.text() == ""


def test_typing_a_value_by_hand(qtbot, store) -> None:
    job = store.create_job("x", board_id=BOARD).id
    panel = _panel(qtbot, store, job)
    panel.from_meter.setChecked(False)
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("ohm"))
    for text, expected in (("1,5 Ω", 1.5), ("OL", None)):
        panel.point_edit.setText("R1")
        panel.value_edit.setText(text)
        panel.record()
        assert store.list_readings(job)[-1].value == expected
    panel.value_edit.setText("lots")
    panel.point_edit.setText("R1")
    panel.record()
    assert "Type a number" in panel.status.text() and len(store.list_readings(job)) == 2


def test_no_meter_says_so(qtbot, store) -> None:
    job = store.create_job("x").id
    panel = _panel(qtbot, store, job)
    panel.point_edit.setText("TP1")
    panel.record()
    assert "No meter reading" in panel.status.text() and store.list_readings(job) == []


def test_without_a_board_number_it_saves_and_says_why_theres_no_comparison(qtbot, store) -> None:
    job = store.create_job("no board").id
    panel = _panel(qtbot, store, job)
    assert "No board number" in panel.board_label.text()
    panel.update_meter(dmm("DIODE", 0.4))
    panel.point_edit.setText("TP1")
    panel.record()
    assert len(store.list_readings(job)) == 1


def test_known_good_while_recording_and_afterwards(qtbot, store) -> None:
    good = store.create_job("good", board_id=BOARD).id
    panel = _panel(qtbot, store, good)
    panel.update_meter(dmm("DIODE", 0.41))
    panel.reference_check.setChecked(True)
    panel.point_edit.setText("PP3V3")
    panel.record()
    panel.reference_check.setChecked(False)
    panel.point_edit.setText("PP5V")
    panel.record()
    assert [r.point for r in store.reference_readings(BOARD)] == ["PP3V3"]
    panel.mark_known_good()
    assert [r.point for r in store.reference_readings(BOARD)] == ["PP3V3", "PP5V"]


def test_clicking_a_checklist_row_fills_the_point(qtbot, store) -> None:
    good_board(store, {"PP1V8": 0.55})
    job = store.create_job("x", board_id=BOARD).id
    panel = _panel(qtbot, store, job)
    panel._pick_row(0, 0)
    assert panel.point_edit.text() == "PP1V8"


def test_delete_a_reading(qtbot, store) -> None:
    job = store.create_job("x").id
    panel = _panel(qtbot, store, job)
    panel.update_meter(dmm("DIODE", 0.4))
    panel.point_edit.setText("TP1")
    panel.record()
    panel.table.selectRow(0)
    panel.delete_selected()
    assert store.list_readings(job) == []


def test_no_job_selected(qtbot, store) -> None:
    panel = _panel(qtbot, store, None)
    assert not panel.record_button.isEnabled()
    assert "Select a job" in panel.board_label.text()


# --- the report and the window ---------------------------------------------------


def test_the_report_lists_measurements(store) -> None:
    good_board(store, {"PP3V3_S5": 0.412, "PP5V": 0.38})
    job = store.create_job("bad", board_id=BOARD).id
    store.add_reading(job, "PP3V3_S5", "diode", 0.002, unit="V")
    report = export_report(store, job, generated_at="2026-10-14T10:00:00+00:00")
    assert "## Measurements" in report
    assert "| PP3V3_S5 | Diode | 0.002 V | 0.412 V | LOW |" in report
    assert "PP5V" not in report  # unmeasured checklist points stay out of the report


def test_a_report_without_readings_has_no_section(store) -> None:
    job = store.create_job("x").id
    assert "Measurements" not in export_report(store, job, generated_at="2026-10-14T10:00:00")


def test_the_window_feeds_the_meter_and_follows_the_job(qtbot) -> None:
    store = JobStore(":memory:")
    window = MainWindow(Config(), job_store=store)
    qtbot.addWidget(window)
    job = store.create_job("x", board_id=BOARD).id
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job)
    window.on_telemetry(TelemetrySnapshot(dmm=dmm("DIODE", 0.512), voltage=0.0, current=0.0))
    panel = window.readings_panel
    assert panel.value_edit.text() == "0.512 V"
    panel.point_edit.setText("TP7")
    panel.record()
    assert store.list_readings(job)[0].value == 0.512
