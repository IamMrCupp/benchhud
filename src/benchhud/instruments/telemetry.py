"""A resilient telemetry snapshot over the OWON driver.

:func:`read_snapshot` gathers the DMM reading + PSU V/I in one call and, crucially,
never raises: a serial hiccup becomes an :class:`TelemetrySnapshot` with ``error``
set, so the GUI keeps running (and can show a stale/error state) instead of
crashing mid-stream. This is Qt-free and driver-agnostic, so it's tested against
a fake-serial-backed :class:`OwonSpm8104`.
"""

from __future__ import annotations

from dataclasses import dataclass

from benchhud.instruments.owon_spm8104 import DmmReading, OwonSpm8104


@dataclass(frozen=True)
class TelemetrySnapshot:
    """One poll of the instrument. ``error`` set means the read failed."""

    dmm: DmmReading | None = None
    voltage: float | None = None
    current: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def read_snapshot(owon: OwonSpm8104) -> TelemetrySnapshot:
    """Read DMM + PSU V/I. Returns an error snapshot instead of raising."""
    try:
        return TelemetrySnapshot(
            dmm=owon.read_dmm(),
            voltage=owon.measure_voltage(),
            current=owon.measure_current(),
        )
    except Exception as exc:
        return TelemetrySnapshot(error=str(exc))
