"""Known-good readings, compared (#235).

Board-level diagnosis is comparison: diode mode to ground on each rail, read
against the same point on a good board. A rail that reads 0.002 V where a good
board reads 0.412 V is shorted. This turns the readings in the job store into
that comparison, one row per test point.

Pure: readings in, rows out. The panel and the report both use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import median

from benchhud.jobs.store import Reading


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    unit: str
    floor: float  # smallest difference that counts, so near-zero readings don't flap


#: The modes readings are compared in. Anything else is stored and shown, but
#: not compared.
MODES: tuple[Mode, ...] = (
    Mode("diode", "Diode", "V", 0.02),
    Mode("ohm", "Resistance", "Ω", 2.0),
    Mode("vdc", "Voltage DC", "V", 0.05),
)
_BY_KEY = {m.key: m for m in MODES}

#: How far from the reference a reading may be: 10%, or the mode's floor.
TOLERANCE = 0.10

#: The OWON's DMM mode tokens, mapped to the modes above. The SPM8104 reports
#: ``VOLT:DC`` and ``DIOD`` (#283); the short forms are kept for older records.
_DMM_MODES = {
    "DIOD": "diode",
    "DIODE": "diode",
    "RES": "ohm",
    "OHM": "ohm",
    "CONT": "ohm",
    "VOLT:DC": "vdc",
    "VDC": "vdc",
}


def mode_from_dmm(token: str) -> str | None:
    """The comparison mode for a DMM mode token, or None if it isn't one."""
    return _DMM_MODES.get(token.strip().upper())


def mode(key: str) -> Mode | None:
    return _BY_KEY.get(key)


class Verdict(StrEnum):
    OK = "OK"
    LOW = "LOW"
    HIGH = "HIGH"
    OPEN = "OPEN"  # this board reads open where the good one doesn't
    TODO = "TODO"  # the good board has it; this job hasn't measured it yet
    NEW = "—"  # nothing to compare with


@dataclass(frozen=True)
class Row:
    point: str
    mode: str
    reading: Reading | None  # this job's latest at the point, if measured
    reference: float | None  # the known-good median
    reference_open: bool  # the known-good board reads open here
    reference_count: int
    verdict: Verdict

    @property
    def flagged(self) -> bool:
        return self.verdict in (Verdict.LOW, Verdict.HIGH, Verdict.OPEN)


def format_value(value: float | None, status: str | None, unit: str) -> str:
    if value is None:
        return status or "—"
    return f"{value:.3f} {unit}".strip() if abs(value) < 100 else f"{value:.1f} {unit}".strip()


def _verdict(reading: Reading | None, ref: float | None, ref_open: bool, count: int) -> Verdict:
    if count == 0:
        return Verdict.NEW
    if reading is None:
        return Verdict.TODO
    if reading.value is None:  # this board reads open (OL)
        return Verdict.OK if ref_open else Verdict.OPEN
    if ref_open:
        return Verdict.LOW  # conducts where the good board is open
    assert ref is not None
    found = mode(reading.mode)
    floor = found.floor if found else 0.0
    if abs(reading.value - ref) <= max(TOLERANCE * abs(ref), floor):
        return Verdict.OK
    return Verdict.LOW if reading.value < ref else Verdict.HIGH


def compare(readings: list[Reading], references: list[Reading]) -> list[Row]:
    """One row per (point, mode): this job's points in the order measured, then
    the known-good points still to do, alphabetically."""
    latest: dict[tuple[str, str], Reading] = {}
    for reading in readings:  # oldest first, so the last one wins
        latest[(reading.point, reading.mode)] = reading
    by_key: dict[tuple[str, str], list[Reading]] = {}
    for ref in references:
        by_key.setdefault((ref.point, ref.mode), []).append(ref)

    def row(key: tuple[str, str], reading: Reading | None) -> Row:
        refs = by_key.get(key, [])
        values = [r.value for r in refs if r.value is not None]
        ref_open = len(refs) > 0 and len(values) * 2 < len(refs)  # most read open
        ref = median(values) if values and not ref_open else None
        return Row(
            point=key[0],
            mode=key[1],
            reading=reading,
            reference=ref,
            reference_open=ref_open,
            reference_count=len(refs),
            verdict=_verdict(reading, ref, ref_open, len(refs)),
        )

    rows = [row(key, reading) for key, reading in latest.items()]
    todo = sorted(key for key in by_key if key not in latest)
    rows += [row(key, None) for key in todo]
    return rows
