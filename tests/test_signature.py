"""Power-on current signatures (#236): recording, comparing, drawing."""

from __future__ import annotations

from benchhud.instruments.signature import (
    Comparison,
    Point,
    Signature,
    SignatureRecorder,
    Verdict,
    compare,
)

POLL = 0.2


def curve(shape, duration: float = 10.0) -> Signature:
    """A signature sampled at the OWON's poll rate from ``shape(t) -> amps``."""
    steps = round(duration / POLL)
    return Signature(tuple(Point(i * POLL, shape(i * POLL), 12.0) for i in range(steps + 1)))


def healthy(t: float) -> float:
    """Steps up through the rails, settles at 0.40 A."""
    if t < 0.4:
        return 0.9  # inrush
    if t < 2.0:
        return 0.08
    if t < 4.0:
        return 0.25
    return 0.40


# --- recording ---------------------------------------------------------------------


def test_records_for_the_window_then_hands_back_the_signature() -> None:
    recorder = SignatureRecorder(window=1.0)
    assert recorder.feed(5.0, 12.0, 0.1) is None  # not started: ignored
    recorder.start(10.0)
    signature = None
    t = 10.0
    while signature is None:
        signature = recorder.feed(t, 12.0, 0.25)
        t += POLL
    assert not recorder.recording
    assert signature.points[0].t == 0.0 and signature.duration >= 1.0
    assert signature.points[-1].current == 0.25


def test_cancel_drops_the_recording() -> None:
    recorder = SignatureRecorder(window=1.0)
    recorder.start(0.0)
    recorder.feed(0.2, 12.0, 0.1)
    recorder.cancel()
    assert not recorder.recording and recorder.feed(5.0, 12.0, 0.1) is None


def test_summary_numbers() -> None:
    signature = curve(healthy)
    assert signature.peak == 0.9
    assert signature.settled == 0.40
    assert signature.current_at(3.0) == 0.25
    assert abs(signature.current_at(0.1) - 0.9) < 1e-9


def test_json_and_csv_round_trip() -> None:
    signature = curve(healthy, duration=1.0)
    assert Signature.from_json(signature.to_json()) == signature
    csv = signature.to_csv().splitlines()
    assert csv[0] == "t_s,current_a,voltage_v" and csv[1] == "0.000,0.9000,12.000"


# --- comparing ---------------------------------------------------------------------


def test_no_reference_is_new() -> None:
    assert compare(curve(healthy), []).verdict is Verdict.NEW


def test_a_healthy_board_matches_despite_inrush_and_noise() -> None:
    def noisy(t: float) -> float:
        return healthy(t) * (1.08 if int(t / POLL) % 2 else 0.94)

    assert compare(curve(noisy), [curve(healthy)]).verdict is Verdict.OK


def test_a_dead_pmic_reads_low() -> None:
    result = compare(curve(lambda t: 0.9 if t < 0.4 else 0.03), [curve(healthy)])
    assert result.verdict is Verdict.LOW
    assert result.at == 1.0 and result.current == 0.03
    assert "LOW from 1.0 s" in result.describe()


def test_a_short_reads_high_where_it_starts() -> None:
    def shorted(t: float) -> float:
        return healthy(t) if t < 3.0 else 1.5

    result = compare(curve(shorted), [curve(healthy)])
    assert result.verdict is Verdict.HIGH and result.at == 3.0


def test_one_bad_poll_does_not_trip_it() -> None:
    def glitch(t: float) -> float:
        return 2.0 if abs(t - 5.0) < 0.05 else healthy(t)

    assert compare(curve(glitch), [curve(healthy)]).verdict is Verdict.OK


def test_the_reference_is_the_median_of_good_boards() -> None:
    goods = [curve(healthy), curve(healthy), curve(lambda t: 1.2)]  # one odd board
    assert compare(curve(healthy), goods).verdict is Verdict.OK


def test_a_shorter_recording_compares_over_what_it_has() -> None:
    assert compare(curve(healthy, duration=5.0), [curve(healthy)]).verdict is Verdict.OK


def test_describe_ok_and_new() -> None:
    assert "matches" in Comparison(Verdict.OK).describe()
    assert "no known-good" in Comparison(Verdict.NEW).describe()


# --- drawing -----------------------------------------------------------------------


def test_render_draws_both_curves(qapp) -> None:
    from benchhud.shell.signature_image import render_signature

    image = render_signature(curve(healthy), [curve(lambda t: 0.5)], title="820-00239")
    assert (image.width(), image.height()) == (640, 260)
    colours = {image.pixelColor(x, y).name() for x in range(0, 640, 4) for y in range(0, 260, 4)}
    assert len(colours) > 4  # background, grid, text, the two curves
    render_signature(Signature(()))  # an empty one doesn't raise


# --- storage and the window ----------------------------------------------------------

BOARD = "820-00239"


def test_store_keeps_signatures_and_references_by_board(tmp_path) -> None:
    from benchhud.jobs.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    good = store.create_job("good", board_id=BOARD).id
    other = store.create_job("other board", board_id="820-01234").id
    stored = store.add_signature(good, curve(healthy).to_json())
    store.add_signature(other, curve(healthy).to_json(), reference=True)
    assert store.reference_signatures(BOARD) == []  # not marked yet
    store.set_signature_reference(stored.id, True)
    [ref] = store.reference_signatures(BOARD)
    assert Signature.from_json(ref.points) == curve(healthy)
    assert store.reference_signatures(BOARD, exclude_job=good) == []
    assert len(store.list_signatures(good)) == 1
    store.delete_job(good)
    assert store.reference_signatures(BOARD) == []
    store.close()


class _Done:
    def exception(self):
        return None


def _window(qtbot, tmp_path):
    from benchhud.config import Config
    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    store = JobStore(tmp_path / "jobs.db")
    window = MainWindow(Config(), job_store=store)
    qtbot.addWidget(window)
    window._signature_recorder.window = 2.0
    return window, store


def _power_on(window, shape, start: float = 100.0) -> None:
    from benchhud.instruments.telemetry import TelemetrySnapshot

    window.on_telemetry(TelemetrySnapshot(voltage=0.0, current=0.0), start)
    window._supply_command_done("Output ON", _Done())  # the supply said yes
    for i in range(1, 12):
        t = i * POLL
        window.on_telemetry(TelemetrySnapshot(voltage=12.0, current=shape(t)), start + t)


def test_no_telemetry_no_recording(qtbot, tmp_path) -> None:
    window, _ = _window(qtbot, tmp_path)
    window.start_signature()
    assert "No supply readings" in window.power_panel.signature.status.text()


def test_output_on_records_compares_and_files_it(qtbot, tmp_path) -> None:
    window, store = _window(qtbot, tmp_path)
    good = store.create_job("good", board_id=BOARD).id
    store.set_signature_reference(store.add_signature(good, curve(healthy).to_json()).id, True)
    job = store.create_job("dead", board_id=BOARD).id
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job)

    _power_on(window, lambda t: 0.9 if t < 0.4 else 0.03)

    [note] = store.list_notes(job)
    assert note.text.startswith("Power-on signature: peak 0.900 A")
    assert "LOW from 1.0 s" in note.text
    assert note.attachment.endswith(".png")
    directory = store.attachments_dir(job)
    assert len(list(directory.glob("power-on-*.png"))) == 1
    assert len(list(directory.glob("power-on-*.csv"))) == 1
    assert len(store.list_signatures(job)) == 1
    assert window.power_panel.chart.reference_series.count() > 0  # good curve drawn
    assert "LOW" in window.power_panel.signature.status.text()


def test_mark_the_last_curve_known_good(qtbot, tmp_path) -> None:
    window, store = _window(qtbot, tmp_path)
    job = store.create_job("good", board_id=BOARD).id
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job)
    _power_on(window, healthy)
    assert "no known-good curve" in store.list_notes(job)[0].text
    assert window.power_panel.signature.good_button.isEnabled()
    window.mark_signature_known_good()
    assert len(store.reference_signatures(BOARD)) == 1


def test_without_a_job_it_says_how_to_keep_it(qtbot, tmp_path) -> None:
    window, _ = _window(qtbot, tmp_path)
    _power_on(window, healthy)
    assert "Select a job to keep it" in window.power_panel.signature.status.text()
    assert not window.power_panel.signature.good_button.isEnabled()


def test_no_board_number_cant_be_a_reference(qtbot, tmp_path) -> None:
    window, store = _window(qtbot, tmp_path)
    job = store.create_job("no board").id
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job)
    _power_on(window, healthy)
    window.mark_signature_known_good()
    assert "no board number" in window.power_panel.signature.status.text()
