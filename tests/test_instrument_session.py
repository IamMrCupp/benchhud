"""Tests for the single-owner instrument session (hardware-free).

The interesting assertions are about *ordering on the wire*: `FakeSerialPort`
records every line written, so a transcript is enough to prove that commands
jump ahead of polls and that a refused command never reached the instrument.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

import pytest

from benchhud.instruments.owon_spm8104 import InstrumentLimitError, from_serial_port
from benchhud.instruments.session import InstrumentSession, InstrumentSessionStopped
from conftest import OWON_SPM8104_RESPONSES, FakeSerialPort

# A poll interval long enough that the loop only polls again when woken — which
# is what makes the ordering assertions deterministic rather than timing-lucky.
SLOW_POLL = 5.0


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def session(fake_owon_serial):
    """A started session; always stopped without closing the shared fake port."""
    driver = from_serial_port(fake_owon_serial, control_enabled=True)
    s = InstrumentSession(driver, poll_interval=SLOW_POLL)
    s.start()
    assert _wait_for(lambda: s.latest() is not None), "first poll never landed"
    yield s
    s.stop(close_driver=False)


# --- polling -------------------------------------------------------------


def test_latest_is_none_before_the_first_poll(fake_owon_serial) -> None:
    s = InstrumentSession(from_serial_port(fake_owon_serial))
    assert s.latest() is None


def test_polling_publishes_a_snapshot(session) -> None:
    snapshot = session.latest()
    assert snapshot.ok
    assert snapshot.voltage == 3.300
    assert snapshot.current == 0.150
    assert snapshot.dmm.mode == "CONT"


# --- the ordering guarantee ---------------------------------------------


def test_commands_run_before_the_next_poll(session, fake_owon_serial) -> None:
    """The whole point of the queue: a button press doesn't wait out a poll."""
    after_first_poll = len(fake_owon_serial.written)

    session.submit(lambda d: d.output_on()).result(timeout=2)
    assert _wait_for(lambda: fake_owon_serial.written.count("MEAS:VOLT?") >= 2)

    transcript = fake_owon_serial.written[after_first_poll:]
    assert transcript[0] == "OUTP ON"


def test_a_command_never_interleaves_with_a_poll_exchange(session, fake_owon_serial) -> None:
    """Each poll's three queries stay contiguous — no write lands inside one."""
    for _ in range(3):
        session.submit(lambda d: d.output_off()).result(timeout=2)
    assert _wait_for(lambda: fake_owon_serial.written.count("MEAS:VOLT?") >= 3)

    written = list(fake_owon_serial.written)
    for i, line in enumerate(written):
        if line == "CONFigure:ALL?":
            assert written[i + 1 : i + 3] == ["MEAS:VOLT?", "MEAS:CURR?"]


def test_submit_returns_the_action_result(session) -> None:
    assert session.submit(lambda d: d.identify()).result(timeout=2).startswith("OWON,SPM8104")


def test_failed_polls_count_up_and_a_good_one_clears_them(fake_owon_serial) -> None:
    """The thread outlives the instrument, so the count is how anyone can tell (#527)."""
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=0.01)
    s.start()
    try:
        assert _wait_for(lambda: s.latest() is not None and s.latest().ok)
        assert s.failed_polls == 0
        fake_owon_serial.silent = True
        assert _wait_for(lambda: s.failed_polls >= 3)
        assert s.is_running and not s.latest().ok
        fake_owon_serial.silent = False
        assert _wait_for(lambda: s.failed_polls == 0)
    finally:
        s.stop(close_driver=False)


# --- failure handling ----------------------------------------------------


def test_a_refused_command_is_attempted_once_and_never_retried(session) -> None:
    """Non-idempotent writes get exactly one attempt — a retried OUTP ON is the
    electrical version of double-counting stock."""
    attempts: list[int] = []

    def boom(driver) -> None:
        attempts.append(1)
        raise InstrumentLimitError("12.0V exceeds the configured 5.5V ceiling")

    future = session.submit(boom)
    with pytest.raises(InstrumentLimitError, match="exceeds the configured"):
        future.result(timeout=2)
    assert attempts == [1]


def test_a_refused_command_does_not_stop_the_session(session) -> None:
    with pytest.raises(InstrumentLimitError):
        session.submit(lambda d: d.set_voltage(99.0)).result(timeout=2)
    assert session.is_running
    assert session.submit(lambda d: d.identify()).result(timeout=2)


def test_a_refused_setpoint_never_reaches_the_wire(session, fake_owon_serial) -> None:
    with pytest.raises(InstrumentLimitError):
        session.submit(lambda d: d.set_voltage(99.0)).result(timeout=2)
    assert not any(line.startswith("VOLT ") for line in fake_owon_serial.written)


# --- lifecycle -----------------------------------------------------------


def test_submit_to_a_session_that_never_started_fails_on_the_future(fake_owon_serial) -> None:
    s = InstrumentSession(from_serial_port(fake_owon_serial))
    future = s.submit(lambda d: d.output_on())
    with pytest.raises(InstrumentSessionStopped):
        future.result(timeout=1)
    assert fake_owon_serial.written == []


def test_stop_resolves_a_queued_command_instead_of_hanging(fake_owon_serial) -> None:
    """Reaching into the queue directly: the invariant is that no caller is left
    blocked on a future once the owner thread is gone."""
    s = InstrumentSession(from_serial_port(fake_owon_serial))
    future: Future[None] = Future()
    s._queue.put((lambda d: None, future, False))

    s.stop(close_driver=False)

    with pytest.raises(InstrumentSessionStopped):
        future.result(timeout=1)


def test_a_command_queued_behind_a_running_one_does_not_run_after_stop(
    session, fake_owon_serial
) -> None:
    """An Output ON that was still waiting must not energize a rail on the way out (#530)."""
    started = threading.Event()

    def slow(_driver) -> None:
        started.set()
        session._stop.wait(5)  # until the stop itself: no sleep to outrun

    running = session.submit(slow)
    queued = session.submit(lambda d: d.apply_output(True))
    assert started.wait(2)

    session.stop(close_driver=False)

    running.result(timeout=1)  # the one in flight is allowed to finish
    with pytest.raises(InstrumentSessionStopped):
        queued.result(timeout=1)
    assert "OUTP ON" not in fake_owon_serial.written
    assert fake_owon_serial.written[-1] == "OUTP OFF"


def test_a_queued_output_off_still_runs_after_stop(fake_owon_serial) -> None:
    """Read-only, so the session won't drop the rail itself: the press has to land (#530)."""
    fake_owon_serial.state["OUTP"] = "ON"  # left on from the front panel
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
    s.start()
    started = threading.Event()

    def slow(_driver) -> None:
        started.set()
        s._stop.wait(5)

    s.submit(slow)
    off = s.submit(lambda d: d.apply_output(False), even_when_stopping=True)
    assert started.wait(2)

    s.stop(close_driver=False)

    off.result(timeout=1)
    assert fake_owon_serial.state["OUTP"] == "OFF"


def test_an_output_off_submitted_just_before_stop_still_runs(fake_owon_serial) -> None:
    """Nothing in flight this time: the stop lands while the press is only queued."""
    fake_owon_serial.state["OUTP"] = "ON"
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
    s.start()
    assert _wait_for(lambda: s.latest() is not None)

    off = s.submit(lambda d: d.apply_output(False), even_when_stopping=True)
    s.stop(close_driver=False)

    off.result(timeout=1)
    assert fake_owon_serial.state["OUTP"] == "OFF"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_thread_that_dies_runs_nothing_that_was_queued(monkeypatch, fake_owon_serial) -> None:
    """Not only stop(): whatever takes the loop down, a waiting Output ON stays unsent (#530)."""
    s = InstrumentSession(
        from_serial_port(fake_owon_serial, control_enabled=True), poll_interval=SLOW_POLL
    )
    queued: list[Future] = []

    def poll_that_blows_up(_driver):
        queued.append(s.submit(lambda d: d.apply_output(True)))
        raise RuntimeError("not a serial error")

    monkeypatch.setattr("benchhud.instruments.session.read_snapshot", poll_that_blows_up)
    s.start()
    try:
        assert _wait_for(lambda: not s.is_running)
        with pytest.raises(InstrumentSessionStopped):
            queued[0].result(timeout=1)
        assert "OUTP ON" not in fake_owon_serial.written
        assert fake_owon_serial.written[-1] == "OUTP OFF"
    finally:
        s.stop(close_driver=False)


# --- a command that outlives stop()'s wait (#532) -------------------------


def _held(s: InstrumentSession):
    """Submit a command that holds the port until the returned event is set."""
    started, release = threading.Event(), threading.Event()

    def hold(_driver) -> None:
        started.set()
        release.wait(5)

    future = s.submit(hold)
    assert started.wait(2)
    return future, release


def test_a_command_that_outlives_the_join_still_gets_its_output_off(fake_owon_serial) -> None:
    """The port isn't closed under the thread, so the shutdown drop is written."""
    s = InstrumentSession(
        from_serial_port(fake_owon_serial, control_enabled=True), poll_interval=SLOW_POLL
    )
    s.start()
    running, release = _held(s)

    s.stop(timeout=0.05)

    assert not s.is_running  # takes no new work...
    assert not fake_owon_serial.closed  # ...but the port is still the thread's
    release.set()
    assert s.wait_finished(2)
    running.result(timeout=1)
    assert fake_owon_serial.written[-1] == "OUTP OFF"
    assert fake_owon_serial.closed


def test_an_output_off_queued_behind_it_is_still_written(fake_owon_serial) -> None:
    """Read-only, so that press is the only thing that drops the rail."""
    fake_owon_serial.state["OUTP"] = "ON"
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
    s.start()
    _, release = _held(s)
    off = s.submit(lambda d: d.apply_output(False), even_when_stopping=True)

    s.stop(timeout=0.05)
    release.set()

    off.result(timeout=2)
    assert fake_owon_serial.state["OUTP"] == "OFF"
    assert s.wait_finished(2)
    assert fake_owon_serial.closed


def test_a_command_queued_behind_it_still_fails(fake_owon_serial) -> None:
    s = InstrumentSession(
        from_serial_port(fake_owon_serial, control_enabled=True), poll_interval=SLOW_POLL
    )
    s.start()
    _, release = _held(s)
    on = s.submit(lambda d: d.apply_output(True))

    s.stop(timeout=0.05)
    release.set()

    with pytest.raises(InstrumentSessionStopped):
        on.result(timeout=2)
    assert "OUTP ON" not in fake_owon_serial.written


def test_close_driver_false_still_holds_when_the_thread_finishes_late(fake_owon_serial) -> None:
    s = InstrumentSession(
        from_serial_port(fake_owon_serial, control_enabled=True), poll_interval=SLOW_POLL
    )
    s.start()
    _, release = _held(s)

    s.stop(close_driver=False, timeout=0.05)
    release.set()

    assert s.wait_finished(2)
    assert fake_owon_serial.written[-1] == "OUTP OFF"
    assert not fake_owon_serial.closed


class _Abort(BaseException):
    """Not an Exception, so nothing on the session thread catches it (#540)."""


class _AbortingPort(FakeSerialPort):
    """Raises :class:`_Abort` on an ``OUTP OFF`` write once ``armed`` is set."""

    def __init__(self) -> None:
        super().__init__(OWON_SPM8104_RESPONSES, supply_state=True)
        self.armed = False

    def write(self, data: bytes) -> int:
        if self.armed and data.decode().strip() == "OUTP OFF":
            raise _Abort("cut short")
        return super().write(data)


class _AbortingClose(FakeSerialPort):
    def close(self) -> None:
        raise _Abort("close cut short")


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_shutdown_drop_cut_short_still_lets_go_of_the_port() -> None:
    """The close and the "finished" still happen, or every waiter sits out its timeout."""
    port = _AbortingPort()
    s = InstrumentSession(from_serial_port(port, control_enabled=True), poll_interval=SLOW_POLL)
    s.start()
    _, release = _held(s)
    thread = s._thread
    s.stop(timeout=0.05)  # the close is the thread's now

    port.armed = True
    release.set()

    assert s.wait_finished(2)
    assert port.closed
    thread.join(2)  # so its _Abort is reported here, not in the next test


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_close_cut_short_still_counts_as_finished() -> None:
    port = _AbortingClose(OWON_SPM8104_RESPONSES, supply_state=True)
    s = InstrumentSession(from_serial_port(port), poll_interval=SLOW_POLL)
    s.start()
    _, release = _held(s)
    thread = s._thread
    s.stop(timeout=0.05)
    release.set()

    assert s.wait_finished(2)
    thread.join(2)


def _abort(_driver) -> None:
    raise _Abort("cut short")


def _poll_that_goes_down(s: InstrumentSession, *actions) -> tuple[list[Future], Callable]:
    """A poll that queues ``actions`` even_when_stopping, then takes the loop down.

    The loop's own drain never sees them, so it's the shutdown's drain that does.
    """
    queued: list[Future] = []

    def poll(_driver):
        queued.extend(s.submit(a, even_when_stopping=True) for a in actions)
        raise _Abort("takes the loop down")

    return queued, poll


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_command_that_cuts_the_shutdown_short_still_drops_the_rail(
    monkeypatch, fake_owon_serial
) -> None:
    """The press behind it, the shutdown drop and the close all still happen."""
    s = InstrumentSession(
        from_serial_port(fake_owon_serial, control_enabled=True), poll_interval=SLOW_POLL
    )
    queued, poll = _poll_that_goes_down(s, _abort, lambda d: d.apply_output(False))
    monkeypatch.setattr("benchhud.instruments.session.read_snapshot", poll)
    s.start()
    thread = s._thread

    assert s.wait_finished(2)
    thread.join(2)  # so its _Abort is reported here, not in the next test
    aborting, behind = queued
    with pytest.raises(_Abort):
        aborting.result(timeout=1)  # its caller hears, rather than waiting forever
    behind.result(timeout=1)
    # On connect, the press behind the raise, and the shutdown drop.
    assert fake_owon_serial.written.count("OUTP OFF") == 3
    s.stop()
    assert fake_owon_serial.closed


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_read_only_output_off_behind_one_that_raised_still_lands(
    monkeypatch, fake_owon_serial
) -> None:
    """Read-only, so no shutdown drop: the second press is the one that counts (#542)."""
    fake_owon_serial.state["OUTP"] = "ON"
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
    queued, poll = _poll_that_goes_down(s, _abort, lambda d: d.apply_output(False))
    monkeypatch.setattr("benchhud.instruments.session.read_snapshot", poll)
    s.start()
    thread = s._thread

    assert s.wait_finished(2)
    thread.join(2)
    first, second = queued
    with pytest.raises(_Abort):
        first.result(timeout=1)
    second.result(timeout=1)
    assert fake_owon_serial.state["OUTP"] == "OFF"
    s.stop()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_nothing_unsafe_runs_behind_a_command_that_took_the_loop_down(fake_owon_serial) -> None:
    """No stop() this time: the raise alone means only the safe presses run (#542)."""
    s = InstrumentSession(
        from_serial_port(fake_owon_serial, control_enabled=True), poll_interval=SLOW_POLL
    )
    s.start()
    started, release = threading.Event(), threading.Event()

    def hold_then_abort(_driver) -> None:
        started.set()
        release.wait(5)
        raise _Abort("takes the loop down")

    first = s.submit(hold_then_abort)
    assert started.wait(2)
    on = s.submit(lambda d: d.apply_output(True))
    off = s.submit(lambda d: d.apply_output(False), even_when_stopping=True)
    thread = s._thread
    release.set()

    assert s.wait_finished(2)
    thread.join(2)
    with pytest.raises(_Abort):
        first.result(timeout=1)
    with pytest.raises(InstrumentSessionStopped):
        on.result(timeout=1)
    off.result(timeout=1)
    assert "OUTP ON" not in fake_owon_serial.written
    s.stop()


def test_a_session_still_finishing_cannot_be_restarted(fake_owon_serial) -> None:
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
    s.start()
    _, release = _held(s)
    s.stop(close_driver=False, timeout=0.05)
    try:
        with pytest.raises(RuntimeError):
            s.start()
    finally:
        release.set()
        assert s.wait_finished(2)
    s.start()  # once it has let go, it can run again
    s.stop(close_driver=False)


class _Tap(FakeSerialPort):
    """A second handle on the same wire: both log to one shared list."""

    def __init__(self, name: str, wire: list[tuple[str, str]]) -> None:
        super().__init__(OWON_SPM8104_RESPONSES, supply_state=True)
        self._name = name
        self._wire = wire

    def write(self, data: bytes) -> int:
        n = super().write(data)
        self._wire.append((self._name, self.written[-1]))
        return n


def test_the_next_session_waits_for_the_last_to_let_go_of_the_wire() -> None:
    """Preferences OK opens the port again at once: one wire, one owner (#532)."""
    wire: list[tuple[str, str]] = []
    old_port, new_port = _Tap("old", wire), _Tap("new", wire)
    old = InstrumentSession(
        from_serial_port(old_port, control_enabled=True), poll_interval=SLOW_POLL
    )
    old.start()
    _, release = _held(old)
    old.stop(timeout=0.05)

    new = InstrumentSession(
        from_serial_port(new_port, control_enabled=True), poll_interval=SLOW_POLL
    )
    new.start(after=old)
    try:
        assert not _wait_for(lambda: new_port.written, timeout=0.3)  # still old's turn
        release.set()
        assert _wait_for(lambda: new.latest() is not None)
        owners = [name for name, _ in wire]
        assert owners == sorted(owners, key=lambda name: name == "new")  # all old, then all new
        assert ("old", "OUTP OFF") in wire
        assert old_port.closed
    finally:
        release.set()
        new.stop(close_driver=False)


def test_a_session_stopped_while_waiting_still_waits_its_turn() -> None:
    """OK twice in a row, or OK then quit: the chain must still wait for the first."""
    wire: list[tuple[str, str]] = []
    ports = {name: _Tap(name, wire) for name in "ABC"}

    def session(name: str) -> InstrumentSession:
        driver = from_serial_port(ports[name], control_enabled=True)
        return InstrumentSession(driver, poll_interval=SLOW_POLL)

    a, b, c = session("A"), session("B"), session("C")
    a.start()
    _, release = _held(a)
    a.stop(timeout=0.05)
    b.start(after=a)
    b.stop(timeout=0.05)  # B never got the wire
    c.start(after=b)
    try:
        assert not b.wait_finished(0)  # B's "finished" now means A's too
        assert not _wait_for(lambda: any(n != "A" for n, _ in wire), timeout=0.3)  # not over A
        release.set()
        assert _wait_for(lambda: c.latest() is not None)
        owners = [name for name, _ in wire]
        assert owners == sorted(owners)  # A, then B's shutdown drop, then C
        assert ("B", "OUTP OFF") in wire
        assert ports["A"].closed and ports["B"].closed
    finally:
        release.set()
        c.stop(close_driver=False)


def test_a_second_stop_does_not_take_back_the_close(fake_owon_serial) -> None:
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
    s.start()
    _, release = _held(s)
    s.stop(timeout=0.05)
    s.stop(close_driver=False, timeout=0.05)
    release.set()
    assert s.wait_finished(2)
    assert fake_owon_serial.closed


def test_stop_closes_the_driver_by_default(fake_owon_serial) -> None:
    s = InstrumentSession(from_serial_port(fake_owon_serial))
    s.start()
    s.stop()
    assert fake_owon_serial.closed is True


def test_stop_can_leave_the_driver_open(fake_owon_serial) -> None:
    s = InstrumentSession(from_serial_port(fake_owon_serial))
    s.start()
    s.stop(close_driver=False)
    assert fake_owon_serial.closed is False


def test_start_and_stop_are_idempotent(fake_owon_serial) -> None:
    s = InstrumentSession(from_serial_port(fake_owon_serial))
    s.start()
    s.start()
    assert s.is_running
    s.stop(close_driver=False)
    s.stop(close_driver=False)
    assert not s.is_running


def test_context_manager_runs_and_stops(fake_owon_serial) -> None:
    with InstrumentSession(from_serial_port(fake_owon_serial)) as s:
        assert s.is_running
        assert _wait_for(lambda: s.latest() is not None)
    assert not s.is_running
    assert fake_owon_serial.closed is True


# --- safety invariants ---------------------------------------------------


class _WedgedDriver:
    """A driver whose port has gone away — every call raises."""

    control_enabled = True

    def __init__(self) -> None:
        self.closed = False

    def _fail(self, *_args) -> None:
        raise OSError("port is wedged")

    read_dmm = measure_voltage = measure_current = output_off = _fail

    def close(self) -> None:
        self.closed = True


def test_the_rail_is_dropped_on_connect(fake_owon_serial) -> None:
    """Never inherit whatever the front panel was left at — OUTP OFF, then read."""
    driver = from_serial_port(fake_owon_serial, control_enabled=True)
    s = InstrumentSession(driver, poll_interval=SLOW_POLL)
    s.start()
    try:
        assert _wait_for(lambda: s.latest() is not None)
        assert fake_owon_serial.written[0] == "OUTP OFF"
    finally:
        s.stop(close_driver=False)


def test_the_rail_is_dropped_on_stop(fake_owon_serial) -> None:
    driver = from_serial_port(fake_owon_serial, control_enabled=True)
    s = InstrumentSession(driver, poll_interval=SLOW_POLL)
    s.start()
    assert _wait_for(lambda: s.latest() is not None)
    s.stop(close_driver=False)
    assert fake_owon_serial.written[-1] == "OUTP OFF"


def test_a_read_only_session_writes_nothing(fake_owon_serial) -> None:
    """Control off means genuinely read-only — not even a safety OUTP OFF."""
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
    s.start()
    assert _wait_for(lambda: s.latest() is not None)
    s.stop(close_driver=False)
    assert set(fake_owon_serial.written) <= {"CONFigure:ALL?", "MEAS:VOLT?", "MEAS:CURR?"}


def test_a_wedged_port_does_not_raise_out_of_stop() -> None:
    """Risk #5: the failure path is exactly when the drop fails, and exactly
    when raising would be useless."""
    driver = _WedgedDriver()
    s = InstrumentSession(driver, poll_interval=SLOW_POLL)
    s.start()
    assert _wait_for(lambda: s.latest() is not None)
    assert s.latest().error is not None

    s.stop()  # must not raise
    assert not s.is_running
    assert driver.closed is True
