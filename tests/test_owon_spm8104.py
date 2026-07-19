"""Tests for the OWON SPM8104 driver (hardware-free, via FakeSerialPort)."""

from __future__ import annotations

from benchhud.instruments.owon_spm8104 import (
    DmmReading,
    from_serial_port,
    parse_dmm_config,
)


def test_identify(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial)
    assert owon.identify() == "OWON,SPM8104,25390427,FV:V1.0.0"


def test_read_dmm_continuity_status(fake_owon_serial) -> None:
    # Verified bench response: "CONT,OL,Manual,"
    owon = from_serial_port(fake_owon_serial)
    reading = owon.read_dmm()
    assert reading.mode == "CONT"
    assert reading.value is None
    assert reading.status == "OL"
    assert reading.ranging == "Manual"


def test_read_dmm_numeric_mode(make_fake_serial) -> None:
    port = make_fake_serial({"CONFigure:ALL?": "VDC,3.301,Auto,"})
    reading = from_serial_port(port).read_dmm()
    assert reading.mode == "VDC"
    assert reading.value == 3.301
    assert reading.status is None
    assert reading.ranging == "Auto"


def test_measure_voltage_and_current(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial)
    assert owon.measure_voltage() == 3.300
    assert owon.measure_current() == 0.150


def test_measure_writes_expected_commands(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial)
    owon.measure_voltage()
    owon.measure_current()
    assert fake_owon_serial.written == ["MEAS:VOLT?", "MEAS:CURR?"]


def test_close_propagates(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial)
    owon.close()
    assert fake_owon_serial.closed is True


def test_parse_dmm_config_handles_short_and_empty() -> None:
    empty = parse_dmm_config("")
    assert isinstance(empty, DmmReading)
    assert empty.mode == ""
    assert empty.value is None

    partial = parse_dmm_config("VDC,1.5")
    assert partial.mode == "VDC"
    assert partial.value == 1.5
    assert partial.ranging == ""
