"""Tests for the OWON SPM8104 driver (hardware-free, via FakeSerialPort)."""

from __future__ import annotations

import pytest

from benchhud.instruments.owon_spm8104 import (
    DEFAULT_LIMITS,
    ControlDisabledError,
    DmmReading,
    InstrumentLimitError,
    InstrumentLimits,
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


# --- control commands (#164) ---------------------------------------------
#
# The commands themselves are UNVERIFIED against real hardware — these tests
# pin the *driver's* behavior (what goes on the wire, what never does), which
# is what a bench session will later correct in one place.


def test_set_voltage_writes_command(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial, control_enabled=True)
    owon.set_voltage(3.3)
    assert fake_owon_serial.written == ["VOLT 3.300"]


def test_set_current_limit_writes_command(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial, control_enabled=True)
    owon.set_current_limit(0.5)
    assert fake_owon_serial.written == ["CURR 0.500"]


def test_output_on_off_write_commands(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial, control_enabled=True)
    owon.output_on()
    owon.output_off()
    assert fake_owon_serial.written == ["OUTP ON", "OUTP OFF"]


def test_set_dmm_mode_sends_the_function_form(fake_owon_serial) -> None:
    """CONFigure:<mode> was ignored; FUNCtion:<mode> switches it (#283)."""
    owon = from_serial_port(fake_owon_serial, control_enabled=True)
    owon.set_dmm_mode("VOLT:DC")
    owon.set_dmm_mode("diod")
    assert fake_owon_serial.written == ["FUNCtion:VOLTage:DC", "FUNCtion:DIODe"]


@pytest.mark.parametrize("mode", ["FREQ", "", "VOLT:DC\nOUTP ON"])
def test_only_the_meters_modes_reach_the_wire(fake_owon_serial, mode) -> None:
    owon = from_serial_port(fake_owon_serial, control_enabled=True)
    with pytest.raises(ValueError, match="unknown DMM mode"):
        owon.set_dmm_mode(mode)
    assert fake_owon_serial.written == []


def test_voltage_above_ceiling_never_reaches_the_wire(fake_owon_serial) -> None:
    """The point of the ceiling: refused setpoints are not sent, not clamped."""
    owon = from_serial_port(fake_owon_serial, InstrumentLimits(5.5, 1.0), control_enabled=True)
    with pytest.raises(InstrumentLimitError, match="exceeds the configured"):
        owon.set_voltage(12.0)
    assert fake_owon_serial.written == []


def test_current_above_ceiling_never_reaches_the_wire(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial, InstrumentLimits(5.5, 1.0), control_enabled=True)
    with pytest.raises(InstrumentLimitError, match="exceeds the configured"):
        owon.set_current_limit(3.0)
    assert fake_owon_serial.written == []


def test_negative_setpoint_refused(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial, control_enabled=True)
    with pytest.raises(InstrumentLimitError, match="negative"):
        owon.set_voltage(-1.0)
    assert fake_owon_serial.written == []


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_setpoint_refused(fake_owon_serial, value) -> None:
    owon = from_serial_port(fake_owon_serial, control_enabled=True)
    with pytest.raises(InstrumentLimitError, match="not a finite"):
        owon.set_voltage(value)
    assert fake_owon_serial.written == []


def test_raised_ceiling_allows_a_higher_setpoint(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial, InstrumentLimits(30.0, 5.0), control_enabled=True)
    owon.set_voltage(12.0)
    assert fake_owon_serial.written == ["VOLT 12.000"]


def test_setpoint_exactly_at_ceiling_is_allowed(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial, InstrumentLimits(5.5, 1.0), control_enabled=True)
    owon.set_voltage(5.5)
    assert fake_owon_serial.written == ["VOLT 5.500"]


def test_default_limits_are_conservative() -> None:
    """A driver built without limits must not be an unlimited one."""
    assert DEFAULT_LIMITS.max_voltage == 5.5
    assert DEFAULT_LIMITS.max_current == 1.0


def test_limits_are_readable(fake_owon_serial) -> None:
    limits = InstrumentLimits(max_voltage=20.0, max_current=2.0)
    assert from_serial_port(fake_owon_serial, limits).limits == limits


# --- the control gate ----------------------------------------------------


def test_control_is_off_by_default(fake_owon_serial) -> None:
    """Read-only telemetry is the posture you get unless you ask for more."""
    assert from_serial_port(fake_owon_serial).control_enabled is False


@pytest.mark.parametrize(
    "action",
    [
        lambda d: d.set_voltage(3.3),
        lambda d: d.set_current_limit(0.5),
        lambda d: d.output_on(),
        lambda d: d.set_dmm_mode("VOLT:DC"),
    ],
    ids=["set_voltage", "set_current_limit", "output_on", "set_dmm_mode"],
)
def test_state_changing_commands_refused_when_control_is_off(fake_owon_serial, action) -> None:
    owon = from_serial_port(fake_owon_serial)
    with pytest.raises(ControlDisabledError, match="instrument control is off"):
        action(owon)
    assert fake_owon_serial.written == []


def test_output_off_works_with_control_disabled(fake_owon_serial) -> None:
    """The gate stops the HUD energizing a rail, never de-energizing one."""
    owon = from_serial_port(fake_owon_serial)
    owon.output_off()
    assert fake_owon_serial.written == ["OUTP OFF"]


def test_reads_are_unaffected_by_the_control_gate(fake_owon_serial) -> None:
    owon = from_serial_port(fake_owon_serial)
    assert owon.measure_voltage() == 3.300
    assert owon.read_dmm().mode == "CONT"
