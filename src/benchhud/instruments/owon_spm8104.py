"""Driver for the OWON SPM8104 (PSU + DMM combo) over SCPI.

Verified on the bench (research.md §4): 115200 baud, newline-terminated,
``*IDN?`` → ``OWON,SPM8104,25390427,FV:V1.0.0``, ``CONFigure:ALL?`` →
``CONT,OL,Manual,`` (mode, reading, ranging). The DMM reading is numeric in
measurement modes (e.g. DC volts) and a status token like ``OL`` (over-limit /
open) otherwise — :func:`parse_dmm_config` handles both.

The driver talks to a :class:`~benchhud.instruments.serial_port.LineInstrument`,
so it is fully unit-testable against ``FakeSerialPort`` with no hardware. Opening
the real port is a thin factory (:func:`open`), bench-gated.

Only the verified command surface is modelled here; extending to the full DMM
mode set (and publishing an ``owon-spm8104`` package) is a bench follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchhud.instruments.serial_port import LineInstrument, SerialPort


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

    def __init__(self, instrument: LineInstrument) -> None:
        self._inst = instrument

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

    def close(self) -> None:
        self._inst.close()


def from_serial_port(port: SerialPort) -> OwonSpm8104:
    """Build a driver over an existing :class:`SerialPort` (test seam)."""
    return OwonSpm8104(LineInstrument(port))


def open_device(device: str, *, baud: int = 115200, timeout: float = 1.0) -> OwonSpm8104:
    """Open the real OWON on a serial ``device`` (e.g. ``/dev/ttyUSB0``).

    Bench-gated: requires ``pyserial`` and the device present. The parsing and
    command logic are covered by tests via :func:`from_serial_port`.
    """
    import serial

    port = serial.Serial(device, baudrate=baud, timeout=timeout)
    return from_serial_port(port)
