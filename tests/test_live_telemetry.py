"""The live instrument path in the shell (offscreen, no hardware).

Everything here injects a session factory rather than opening a serial port, so
these run in CI. The one thing they cannot cover is whether the instrument
answers — that's todo 6 of #164.
"""

from __future__ import annotations

import time

from benchhud.config import Config
from benchhud.instruments.owon_spm8104 import from_serial_port
from benchhud.instruments.session import InstrumentSession
from benchhud.shell.main_window import MainWindow


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
