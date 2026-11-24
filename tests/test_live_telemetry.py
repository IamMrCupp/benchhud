"""The live instrument path in the shell (offscreen, no hardware).

Everything here injects a session factory rather than opening a serial port, so
these run in CI. The one thing they cannot cover is whether the instrument
answers — that's todo 6 of #164.
"""

from __future__ import annotations

import threading
import time

from benchhud.config import Config
from benchhud.instruments.owon_spm8104 import from_serial_port
from benchhud.instruments.session import InstrumentSession
from benchhud.shell.main_window import MainWindow
from conftest import OWON_SPM8104_RESPONSES, FakeSerialPort


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _session_for(port, *, control: bool = False) -> InstrumentSession:
    """A session over the shared fake OWON port, polling fast enough for a test."""
    return InstrumentSession(from_serial_port(port, control_enabled=control), poll_interval=0.05)


# --- wiring --------------------------------------------------------------


def test_live_telemetry_reaches_the_meter(qtbot, fake_owon_serial) -> None:
    """The point of todo 3: real snapshots, not DemoSignal, drive the HUD."""
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial)

    window.start_telemetry(lambda: session)
    try:
        assert window.telemetry_active
        assert _wait_for(lambda: session.latest() is not None)
        window._telemetry_tick()
        assert window.power_panel.last_snapshot.voltage == 3.300
    finally:
        window.stop_telemetry()


def test_start_is_idempotent(qtbot, fake_owon_serial) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial)

    window.start_telemetry(lambda: session)
    window.start_telemetry(lambda: session)
    try:
        assert window.telemetry_active
    finally:
        window.stop_telemetry()


def test_stop_is_idempotent_and_drops_the_session(qtbot, fake_owon_serial) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial)

    window.start_telemetry(lambda: session)
    window.stop_telemetry()
    window.stop_telemetry()
    assert not window.telemetry_active
    assert not session.is_running


def test_no_instrument_configured_is_not_an_error(qtbot) -> None:
    """An unplugged bench leaves the HUD running, not refusing to start."""
    window = MainWindow()
    qtbot.addWidget(window)
    window.start_telemetry(lambda: None)
    assert not window.telemetry_active


def test_disabled_telemetry_panel_builds_no_session(qtbot) -> None:
    config = Config()
    config.panels.telemetry.enabled = False
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window._make_instrument_session() is None


def test_a_bad_port_does_not_crash_the_shell(qtbot) -> None:
    config = Config()
    config.panels.telemetry.enabled = True
    config.panels.telemetry.port = "/dev/definitely-not-a-real-port"
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window._make_instrument_session() is None


# --- safety at the shell boundary ---------------------------------------


def test_stopping_telemetry_drops_the_rail(qtbot, fake_owon_serial) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial, control=True)

    window.start_telemetry(lambda: session)
    assert _wait_for(lambda: session.latest() is not None)
    window.stop_telemetry()
    assert fake_owon_serial.written[-1] == "OUTP OFF"


def test_closing_the_window_stops_telemetry(qtbot, fake_owon_serial) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial)

    window.start_telemetry(lambda: session)
    window.close()
    assert not window.telemetry_active
    assert not session.is_running


def _hold(session: InstrumentSession) -> threading.Event:
    """Keep the session's thread in a command until the returned event is set."""
    started, release = threading.Event(), threading.Event()

    def hold(_driver) -> None:
        started.set()
        release.wait(5)

    session.submit(hold)
    assert started.wait(2)
    return release


def test_quitting_waits_for_a_command_to_finish_and_drop_the_rail(
    qtbot, fake_owon_serial, monkeypatch, caplog
) -> None:
    """A daemon thread dies with the process: quit holds on for its OUTP OFF (#532)."""
    monkeypatch.setattr("benchhud.instruments.session.STOP_JOIN_TIMEOUT_S", 0.05)
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial, control=True)
    window.start_telemetry(lambda: session)
    release = _hold(session)
    threading.Timer(1.0, release.set).start()  # finishes after stop() has given up waiting

    with caplog.at_level("WARNING", logger="benchhud.instruments.session"):
        window.close()

    # Proof the quit went through the hand-off, not a stop() that happened to finish.
    assert "still busy" in caplog.text
    assert session.wait_finished(0)
    assert fake_owon_serial.written[-1] == "OUTP OFF"
    assert fake_owon_serial.closed


def test_a_new_session_waits_for_the_stopped_one(qtbot, fake_owon_serial, monkeypatch) -> None:
    """Preferences OK restarts at once; the new port stays quiet until the old lets go (#532)."""
    monkeypatch.setattr("benchhud.instruments.session.STOP_JOIN_TIMEOUT_S", 0.05)
    window = MainWindow()
    qtbot.addWidget(window)
    old = _session_for(fake_owon_serial, control=True)
    window.start_telemetry(lambda: old)
    release = _hold(old)
    window.stop_telemetry()

    other_port = FakeSerialPort(OWON_SPM8104_RESPONSES, supply_state=True)
    new = _session_for(other_port, control=True)
    window.start_telemetry(lambda: new)
    try:
        assert not _wait_for(lambda: other_port.written, timeout=0.3)
        release.set()
        assert _wait_for(lambda: new.latest() is not None)
        assert fake_owon_serial.closed  # the old one closed its own port on the way out
    finally:
        release.set()
        window.stop_telemetry()


# --- demo and live are mutually exclusive --------------------------------


def test_demo_mode_stops_the_live_poll(qtbot, fake_owon_serial) -> None:
    """Both feed on_telemetry; alternating real and synthetic would be worse
    than either."""
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial)

    window.start_telemetry(lambda: session)
    window.set_demo_mode(True)
    try:
        assert not window.telemetry_active
        assert window.demo_active
    finally:
        window.set_demo_mode(False)
        window.stop_telemetry()


def test_live_poll_will_not_start_while_demo_runs(qtbot, fake_owon_serial) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    session = _session_for(fake_owon_serial)

    window.set_demo_mode(True)
    try:
        window.start_telemetry(lambda: session)
        assert not window.telemetry_active
    finally:
        window.set_demo_mode(False)
        window.stop_telemetry()
