"""Driver for the OWON SPM8104 (PSU + DMM combo) over SCPI.

Verified on the bench (research.md §4): 115200 baud, newline-terminated,
``*IDN?`` → ``OWON,SPM8104,25390427,FV:V1.0.0``, ``CONFigure:ALL?`` →
``CONT,OL,Manual,`` (mode, reading, ranging). The DMM reading is numeric in
measurement modes (e.g. DC volts) and a status token like ``OL`` (over-limit /
open) otherwise — :func:`parse_dmm_config` handles both.

The driver talks to a :class:`~benchhud.instruments.serial_port.LineInstrument`,
so it is fully unit-testable against ``FakeSerialPort`` with no hardware. Opening
the real port is a thin factory (:func:`open`), bench-gated.

**Reads are verified; writes are not.** The four queries above were measured on
the bench. Every command that *changes* instrument state (#164) is modelled from
standard SCPI and marked ``UNVERIFIED`` at its call site until a bench session
confirms it — OWON's documentation is thin, and this box is a PSU and a DMM
sharing one command namespace. Don't erase that distinction: a guessed command
that silently does nothing looks identical to a working one from here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from benchhud.instruments.serial_port import LineInstrument, SerialPort


class InstrumentLimitError(Exception):
    """A setpoint was refused before it reached the instrument.

    Raised rather than clamped. Silently lowering 12V to 5.5V would be the worst
    of both worlds — the operator believes one thing is on the rail and something
    else is, which is precisely the mistake a ceiling exists to prevent.
    """


class ControlDisabledError(Exception):
    """A state-changing command was attempted with control turned off.

    Read-only telemetry is the default posture (``[instruments] control``), so a
    HUD that can energize a rail is something the operator switched on.
    """


@dataclass(frozen=True)
class InstrumentLimits:
    """Hard ceilings the driver refuses to exceed.

    Enforced here rather than in the UI on purpose: a UI-only guard is one
    refactor away from being bypassed by a caller that doesn't know it exists.
    """

    max_voltage: float
    max_current: float


#: Deliberately conservative — enough for logic rails and phone-board work, so
#: anything above it has to be a considered config change rather than a default
#: nobody looked at. This is a supply wired to other people's boards.
DEFAULT_LIMITS = InstrumentLimits(max_voltage=5.5, max_current=1.0)


@dataclass(frozen=True)
class DmmReading:
    """A parsed ``CONFigure:ALL?`` response.

    ``value`` is the numeric reading when the DMM is in a measurement mode;
    otherwise it is ``None`` and ``status`` holds the token (e.g. ``"OL"``).
    """

    mode: str
    value: float | None
    status: str | None
    ranging: str
    raw: str


def parse_dmm_config(response: str) -> DmmReading:
    """Parse a ``CONFigure:ALL?`` response like ``"CONT,OL,Manual,"``."""
    fields = [f.strip() for f in response.split(",")]
    mode = fields[0] if len(fields) > 0 else ""
    reading = fields[1] if len(fields) > 1 else ""
    ranging = fields[2] if len(fields) > 2 else ""

    value: float | None = None
    status: str | None = None
    try:
        value = float(reading)
    except ValueError:
        status = reading or None

    return DmmReading(mode=mode, value=value, status=status, ranging=ranging, raw=response)


class OwonSpm8104:
    """SCPI driver for the OWON SPM8104."""

    def __init__(
        self,
        instrument: LineInstrument,
        limits: InstrumentLimits = DEFAULT_LIMITS,
        *,
        control_enabled: bool = False,
    ) -> None:
        self._inst = instrument
        self._limits = limits
        self._control_enabled = control_enabled

    @property
    def limits(self) -> InstrumentLimits:
        """The ceilings this driver enforces on setpoints."""
        return self._limits

    @property
    def control_enabled(self) -> bool:
        """Whether state-changing commands are permitted at all."""
        return self._control_enabled

    def _require_control(self, what: str) -> None:
        if not self._control_enabled:
            raise ControlDisabledError(
                f"{what} refused — instrument control is off ([instruments] control)"
            )

    def identify(self) -> str:
        """Return the ``*IDN?`` identity string."""
        return self._inst.query("*IDN?")

    def read_dmm(self) -> DmmReading:
        """Read the current DMM mode + value via ``CONFigure:ALL?``."""
        return parse_dmm_config(self._inst.query("CONFigure:ALL?"))

    def measure_voltage(self) -> float:
        """PSU output voltage (``MEAS:VOLT?``), in volts."""
        return float(self._inst.query("MEAS:VOLT?"))

    def measure_current(self) -> float:
        """PSU output current (``MEAS:CURR?``), in amperes."""
        return float(self._inst.query("MEAS:CURR?"))

    # --- control (UNVERIFIED — see the module docstring) ------------------

    def _check_setpoint(self, value: float, ceiling: float, unit: str) -> None:
        """Refuse a setpoint before it reaches the instrument."""
        if not math.isfinite(value):
            raise InstrumentLimitError(f"{value} is not a finite {unit} setpoint")
        if value < 0:
            raise InstrumentLimitError(f"{value}{unit} is negative")
        if value > ceiling:
            raise InstrumentLimitError(
                f"{value}{unit} exceeds the configured {ceiling}{unit} ceiling"
            )

    def set_voltage(self, volts: float) -> None:
        """Set the PSU output voltage. UNVERIFIED command."""
        self._require_control("setting voltage")
        self._check_setpoint(volts, self._limits.max_voltage, "V")
        self._inst.command(f"VOLT {volts:.3f}")

    def set_current_limit(self, amps: float) -> None:
        """Set the PSU current limit. UNVERIFIED command."""
        self._require_control("setting current limit")
        self._check_setpoint(amps, self._limits.max_current, "A")
        self._inst.command(f"CURR {amps:.3f}")

    def output_on(self) -> None:
        """Enable the PSU output. UNVERIFIED command."""
        self._require_control("enabling the output")
        self._inst.command("OUTP ON")

    def output_off(self) -> None:
        """Disable the PSU output. UNVERIFIED command.

        **Deliberately exempt from the control gate.** The gate exists to stop
        the HUD energizing a rail, never to stop it de-energizing one — a
        read-only session that somehow found a live output should still be able
        to drop it, and shutdown paths must not depend on a config flag.
        """
        self._inst.command("OUTP OFF")

    def set_dmm_mode(self, mode: str) -> None:
        """Switch the DMM measurement mode (e.g. ``VDC``, ``CONT``). UNVERIFIED.

        The mode is interpolated into a command line, so a newline in it would
        smuggle a second command onto the wire — rejected rather than stripped,
        because a caller passing one is confused about something.
        """
        self._require_control("switching DMM mode")
        token = mode.strip()
        if not token:
            raise ValueError("DMM mode must not be empty")
        if "\r" in token or "\n" in token:
            raise ValueError(f"DMM mode must be a single line, got {mode!r}")
        self._inst.command(f"CONFigure:{token}")

    def close(self) -> None:
        self._inst.close()


def from_serial_port(
    port: SerialPort,
    limits: InstrumentLimits = DEFAULT_LIMITS,
    *,
    control_enabled: bool = False,
) -> OwonSpm8104:
    """Build a driver over an existing :class:`SerialPort` (test seam)."""
    return OwonSpm8104(LineInstrument(port), limits, control_enabled=control_enabled)


def open_device(
    device: str,
    *,
    baud: int = 115200,
    timeout: float = 1.0,
    limits: InstrumentLimits = DEFAULT_LIMITS,
    control_enabled: bool = False,
) -> OwonSpm8104:
    """Open the real OWON on a serial ``device`` (e.g. ``/dev/ttyUSB0``).

    Bench-gated: requires ``pyserial`` and the device present. The parsing and
    command logic are covered by tests via :func:`from_serial_port`.
    """
    import serial

    port = serial.Serial(device, baudrate=baud, timeout=timeout)
    return from_serial_port(port, limits, control_enabled=control_enabled)
