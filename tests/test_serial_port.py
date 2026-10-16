"""Tests for the serial transport seam and SCPI line helper."""

from __future__ import annotations

from benchhud.instruments.serial_port import LineInstrument, SerialPort


def test_fake_satisfies_serial_port_protocol(fake_owon_serial) -> None:
    assert isinstance(fake_owon_serial, SerialPort)


def test_query_returns_stripped_response(fake_owon_serial) -> None:
    inst = LineInstrument(fake_owon_serial)
    assert inst.query("*IDN?") == "OWON,SPM8104,25390427,FV:V1.0.0"


def test_command_appends_terminator_and_is_recorded(fake_owon_serial) -> None:
    inst = LineInstrument(fake_owon_serial)
    inst.command("MEAS:VOLT?")
    assert fake_owon_serial.written == ["MEAS:VOLT?"]


def test_query_writes_then_reads(fake_owon_serial) -> None:
    inst = LineInstrument(fake_owon_serial)
    assert inst.query("MEAS:VOLT?") == "3.300"
    assert inst.query("MEAS:CURR?") == "0.150"
    assert fake_owon_serial.written == ["MEAS:VOLT?", "MEAS:CURR?"]


def test_unknown_command_reads_empty(fake_owon_serial) -> None:
    inst = LineInstrument(fake_owon_serial)
    # No canned reply -> readline drains nothing (timeout semantics).
    assert inst.query("SYST:BOGUS?") == ""


def test_custom_responses_via_factory(make_fake_serial) -> None:
    port = make_fake_serial({"PING?": "PONG"})
    inst = LineInstrument(port)
    assert inst.query("PING?") == "PONG"


def test_close_propagates(fake_owon_serial) -> None:
    inst = LineInstrument(fake_owon_serial)
    inst.close()
    assert fake_owon_serial.closed is True


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def test_commands_are_spaced_so_the_second_isnt_dropped(fake_owon_serial) -> None:
    """The OWON lost CURR when it came right behind VOLT (#280)."""
    clock = _Clock()
    inst = LineInstrument(fake_owon_serial, command_gap=0.1, clock=clock, sleep=clock.sleep)
    inst.command("VOLT 3.300")
    inst.command("CURR 0.250")
    assert clock.slept == [0.1]
    assert fake_owon_serial.written == ["VOLT 3.300", "CURR 0.250"]


def test_no_wait_once_the_gap_has_passed_or_after_a_query(fake_owon_serial) -> None:
    clock = _Clock()
    inst = LineInstrument(fake_owon_serial, command_gap=0.1, clock=clock, sleep=clock.sleep)
    inst.command("VOLT 3.300")
    clock.now += 0.5
    inst.command("CURR 0.250")
    inst.query("MEAS:VOLT?")  # waits for the command before it
    inst.query("MEAS:CURR?")  # but a query leaves nothing to wait for
    assert clock.slept == [0.1]
