"""Tests for the single-owner instrument session (hardware-free).

The interesting assertions are about *ordering on the wire*: `FakeSerialPort`
records every line written, so a transcript is enough to prove that commands
jump ahead of polls and that a refused command never reached the instrument.
"""

from __future__ import annotations

import time
from concurrent.futures import Future

import pytest

from benchhud.instruments.owon_spm8104 import InstrumentLimitError, from_serial_port
from benchhud.instruments.session import InstrumentSession, InstrumentSessionStopped

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
    s = InstrumentSession(from_serial_port(fake_owon_serial), poll_interval=SLOW_POLL)
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
    s._queue.put((lambda d: None, future))

    s.stop(close_driver=False)

    with pytest.raises(InstrumentSessionStopped):
        future.result(timeout=1)


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
