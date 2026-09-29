"""The terminal's serial consoles (#174): a raw port, and the OWON's query console.

The raw console runs real serial I/O over pyserial's ``loop://`` port, which
echoes back whatever is written. The OWON console runs through a real
InstrumentSession on the fake OWON serial port, so "never a second handle on the
port" and "queries only" are checked on the actual path.
"""

from __future__ import annotations

import sys
import time

import pytest
import serial
from PySide6.QtWidgets import QWidget

from benchhud.config import Config
from benchhud.devices.discovery import SerialDevice
from benchhud.instruments.owon_spm8104 import NotAQueryError, from_serial_port
from benchhud.instruments.session import InstrumentSession
from benchhud.jobs.store import JobStore
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.terminal import TerminalPanel
from benchhud.terminal import TerminalHost
from benchhud.terminal.serial_session import OwonConsoleSession, SerialSession

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the terminal is POSIX-only")


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.02)


# --- the driver's guard -----------------------------------------------------------------


@pytest.mark.parametrize("control", [False, True])
def test_raw_query_sends_queries_whatever_control_says(fake_owon_serial, control) -> None:
    driver = from_serial_port(fake_owon_serial, control_enabled=control)
    assert driver.raw_query(" *IDN? ").startswith("OWON,SPM8104")


@pytest.mark.parametrize("line", ["OUTP ON", "VOLT 24", "OUTP ON;*IDN?", "*IDN?\nOUTP ON", ""])
def test_raw_query_refuses_anything_but_one_query_even_with_control_on(
    fake_owon_serial, line
) -> None:
    """A typed setpoint would bypass the driver's ceilings, so it never goes out."""
    driver = from_serial_port(fake_owon_serial, control_enabled=True)
    with pytest.raises(NotAQueryError):
        driver.raw_query(line)
    assert fake_owon_serial.written == []


# --- a raw serial port -------------------------------------------------------------------


class Recorder:
    """Wraps a loop:// port and records exactly what was written to it."""

    def __init__(self, port) -> None:
        self.port = port
        self.written = b""

    def write(self, data: bytes) -> int:
        self.written += data
        return self.port.write(data)

    def __getattr__(self, name):
        return getattr(self.port, name)


def _serial_host(recorders: list, **options) -> TerminalHost:
    def opener(url, **kw):
        recorders.append(Recorder(serial.serial_for_url(url, **kw)))
        return recorders[-1]

    host = TerminalHost(
        ["serial:loop://"],
        share_token="s",
        session_factory=lambda _argv, **kw: SerialSession(
            "loop://", opener=opener, **options, **kw
        ),
    )
    host.start()
    return host


@pytest.mark.parametrize(
    ("ending", "sent"), [("crlf", b"AT\r\n"), ("cr", b"AT\r"), ("lf", b"AT\n")]
)
def test_enter_sends_the_configured_line_ending(ending, sent) -> None:
    ports: list[Recorder] = []
    host = _serial_host(ports, line_ending=ending)
    host.write("AT\r")
    _wait_for(lambda: "AT" in host.read(0)[1])  # the loopback echo came back
    assert ports[0].written == sent
    host.close()


def test_the_device_reply_is_shown_with_bare_lf_as_new_lines() -> None:
    ports: list[Recorder] = []
    host = _serial_host(ports, line_ending="lf")
    host.write("héllo\r")
    _wait_for(lambda: "héllo\r\n" in host.read(0)[1])
    assert "connected to loop:// at 115200 baud" in host.read(0)[1]
    host.close()


def test_local_echo_shows_what_you_type() -> None:
    """For devices that don't echo: here the loop echoes too, so it appears twice."""
    ports: list[Recorder] = []
    host = _serial_host(ports, local_echo=True)
    host.write("x")
    _wait_for(lambda: host.read(0)[1].count("x") == 2)
    host.close()


class Unplugged:
    """A port whose reads fail, as when the USB cable comes out."""

    in_waiting = 0

    def read(self, _n):
        raise serial.SerialException("device reports readiness to read but returned no data")

    def write(self, data):
        return len(data)

    def close(self):
        pass


def test_an_unplugged_port_says_so_and_ends_the_session() -> None:
    host = TerminalHost(
        ["serial:x"],
        share_token="s",
        session_factory=lambda _a, **kw: SerialSession(
            "/dev/x", opener=lambda *_a, **_k: Unplugged(), **kw
        ),
    )
    host.start()
    _wait_for(lambda: host.state().exit_code is not None)
    assert "/dev/x disconnected" in host.read(0)[1]
    assert host.state().exit_code == 1


def test_a_port_that_wont_open_is_an_error_not_a_crash() -> None:
    host = TerminalHost(
        ["serial:/dev/nope"],
        share_token="s",
        session_factory=lambda _a, **kw: SerialSession("/dev/nope-benchhud-test", **kw),
    )
    host.start()
    assert not host.state().running
    assert "/dev/nope" in host.state().error


# --- the OWON's query console -------------------------------------------------------------


@pytest.fixture
def owon_session(fake_owon_serial):
    driver = from_serial_port(fake_owon_serial, control_enabled=True)
    session = InstrumentSession(driver, poll_interval=5.0)
    session.start()
    yield session, fake_owon_serial
    session.stop()


def _owon_host(session) -> TerminalHost:
    host = TerminalHost(
        ["owon:scpi"],
        share_token="s",
        session_factory=lambda _a, **kw: OwonConsoleSession(session, **kw),
    )
    host.start()
    return host


def test_a_query_goes_through_the_session_and_its_reply_is_shown(owon_session) -> None:
    session, _port = owon_session
    host = _owon_host(session)
    assert host.read(0)[1].endswith("owon> ")
    host.write("*IDN?\r")
    _wait_for(lambda: "OWON,SPM8104" in host.read(0)[1])
    assert host.read(0)[1].endswith("owon> ")
    host.close()


def test_a_setpoint_is_refused_and_never_reaches_the_port(owon_session) -> None:
    session, port = owon_session
    host = _owon_host(session)
    host.write("VOLT 24\r")
    _wait_for(lambda: "not sent" in host.read(0)[1])
    # The port only ever sees the session's own traffic (its OUTP OFF on
    # connect, and polls like MEAS:VOLT?), never the typed setpoint.
    assert "VOLT 24" not in port.written
    host.close()


def test_line_editing_backspace_and_ctrl_c(owon_session) -> None:
    session, port = owon_session
    host = _owon_host(session)
    host.write("OUTP\x03")  # Ctrl-C drops the line
    host.write("*IDX\x7fN?\r")  # a typo, fixed with backspace
    _wait_for(lambda: "OWON,SPM8104" in host.read(0)[1])
    assert "*IDN?" in port.written
    assert not [line for line in port.written if line.startswith("OUTP") and line != "OUTP OFF"]
    host.close()


def test_no_instrument_session_means_a_clear_error() -> None:
    host = _owon_host(None)
    assert "isn't connected" in host.state().error


# --- the picker and the window -----------------------------------------------------------------


class Ports:
    """A scanner with the OWON on ttyUSB0 and a second device on ttyUSB1."""

    def cameras(self):
        return []

    def serial_ports(self):
        return [SerialDevice("/dev/ttyUSB0", "CH341"), SerialDevice("/dev/ttyUSB1", "FTDI")]


def test_setting_the_list_never_switches_the_connection(qtbot) -> None:
    panel = TerminalPanel(lambda parent: None)
    qtbot.addWidget(panel)
    seen = []
    panel.target_changed.connect(lambda *args: seen.append(args))
    panel.set_targets([("Shell", "shell"), ("/dev/ttyUSB1", "/dev/ttyUSB1")], "/dev/ttyUSB1")
    assert seen == []
    assert panel.target == "/dev/ttyUSB1"
    assert panel.baud_combo.isVisibleTo(panel) and panel.restart_button.text() == "Reconnect"
    panel.set_targets([("Shell", "shell")], "shell")
    assert not panel.baud_combo.isVisibleTo(panel)
    assert panel.restart_button.text() == "Restart shell"


def _window(qtbot, *, telemetry=True) -> MainWindow:
    config = Config()
    config.browser_sources.port = 0
    config.terminal.enabled = True
    config.panels.telemetry.enabled = telemetry
    config.panels.telemetry.port = "/dev/ttyUSB0"
    window = MainWindow(
        config, job_store=JobStore(":memory:"), scanner=Ports(), terminal_view_factory=FakeView
    )
    qtbot.addWidget(window)
    return window


class FakeView(QWidget):
    def setUrl(self, url) -> None:
        self.url = url.toString()


def test_the_owon_port_is_offered_only_as_its_query_console(qtbot) -> None:
    window = _window(qtbot)
    targets = {value: label for label, value in window.terminal_targets()}
    assert "owon" in targets and "/dev/ttyUSB0" not in targets  # never raw
    assert "/dev/ttyUSB1" in targets
    offline = {value for _label, value in _window(qtbot, telemetry=False).terminal_targets()}
    assert "owon" not in offline


def test_picking_the_owon_port_means_its_console(qtbot) -> None:
    window = _window(qtbot)
    window._config.terminal.target = "/dev/ttyUSB0"
    identity, _factory = window._terminal_target()
    assert identity[0] == "owon:scpi"


def test_picking_a_serial_port_reconnects_the_terminal_to_it(qtbot) -> None:
    window = _window(qtbot, telemetry=False)
    window.start_browser_sources()
    try:
        window.set_terminal_target("loop://", 9600, "lf", False)
        assert window._config.terminal.target == "loop://"
        assert window.terminal.argv[0] == "serial:loop://"
        assert window.terminal.state().running
        assert window.browser_sources is not None  # the server came back with it
    finally:
        window.close()
