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
