"""Power-on current signatures (#236).

What a board draws in the first seconds after power-on says a lot: a short pegs
at the current limit, a dead PMIC sits at a few tens of mA, a healthy board
steps up through its rails and settles. This records that curve from the
supply's telemetry and compares it with known-good curves for the same board.

Pure: samples in, a :class:`Signature` out, and a comparison. The poll rate
(0.2 s on the OWON) is enough for steps, not for inrush.
"""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

#: How long a power-on is recorded, by default.
DEFAULT_WINDOW = 10.0
#: How far from the known-good curve counts: 25% (boot currents vary), or 20 mA.
TOLERANCE = 0.25
FLOOR_A = 0.02
#: Ignore the first second: inrush and the first poll's timing differ run to run.
GRACE_S = 1.0
#: Where the curves are compared, in seconds.
STEP_S = 0.5


@dataclass(frozen=True)
class Point:
    t: float  # seconds since power-on
    current: float
    voltage: float


@dataclass(frozen=True)
class Signature:
    points: tuple[Point, ...]

    @property
    def duration(self) -> float:
        return self.points[-1].t if self.points else 0.0

    @property
    def peak(self) -> float:
        return max((p.current for p in self.points), default=0.0)

    @property
    def settled(self) -> float:
        """The median current over the last quarter: where the board ended up."""
        if not self.points:
            return 0.0
        tail = [p.current for p in self.points if p.t >= self.duration * 0.75]
        return median(tail or [self.points[-1].current])

    def current_at(self, t: float) -> float:
        """Linear interpolation; the ends hold their value."""
        points = self.points
        if not points:
            return 0.0
        times = [p.t for p in points]
        i = bisect_left(times, t)
        if i == 0:
            return points[0].current
        if i >= len(points):
            return points[-1].current
        a, b = points[i - 1], points[i]
        if b.t == a.t:
            return b.current
        return a.current + (b.current - a.current) * (t - a.t) / (b.t - a.t)

    def to_json(self) -> str:
        return json.dumps([[p.t, p.current, p.voltage] for p in self.points])

    @classmethod
    def from_json(cls, text: str) -> Signature:
        return cls(tuple(Point(t, a, v) for t, a, v in json.loads(text)))

    def to_csv(self) -> str:
        rows = ["t_s,current_a,voltage_v"]
        rows += [f"{p.t:.3f},{p.current:.4f},{p.voltage:.3f}" for p in self.points]
        return "\n".join(rows) + "\n"


class SignatureRecorder:
    """Collects telemetry for ``window`` seconds after :meth:`start`."""

    def __init__(self, window: float = DEFAULT_WINDOW) -> None:
        self.window = window
        self._t0: float | None = None
        self._points: list[Point] = []

    @property
    def recording(self) -> bool:
        return self._t0 is not None

    @property
    def elapsed(self) -> float:
        return self._points[-1].t if self._points else 0.0

    def start(self, t0: float) -> None:
        self._t0 = t0
        self._points = []

    def cancel(self) -> None:
        self._t0 = None
        self._points = []

    def feed(self, t: float, voltage: float, current: float) -> Signature | None:
        """Add a sample; returns the signature once the window is full."""
        if self._t0 is None:
            return None
        self._points.append(Point(max(0.0, t - self._t0), current, voltage))
        if self._points[-1].t < self.window:
            return None
        signature = Signature(tuple(self._points))
        self.cancel()
        return signature


class Verdict(StrEnum):
    OK = "OK"
    LOW = "LOW"  # draws less than a good board: a rail not coming up
    HIGH = "HIGH"  # draws more: a short, or something stuck on
    NEW = "—"  # no known-good curve to compare with


@dataclass(frozen=True)
class Comparison:
    verdict: Verdict
    at: float | None = None  # seconds after power-on where it left the good curve
    current: float | None = None  # this board at that moment
    reference: float | None = None  # the good boards at that moment

    def describe(self) -> str:
        if self.verdict is Verdict.NEW:
            return "no known-good curve for this board yet"
        if self.verdict is Verdict.OK:
            return "matches the known-good curve"
        assert self.at is not None and self.current is not None and self.reference is not None
        return (
            f"{self.verdict.value} from {self.at:.1f} s: {self.current:.3f} A "
            f"where a good board draws {self.reference:.3f} A"
        )


def reference_at(references: list[Signature], t: float) -> float:
    return median(r.current_at(t) for r in references)


def compare(signature: Signature, references: list[Signature]) -> Comparison:
    """Compare with the median of the known-good curves.

    It flags the first time this board leaves the band for two steps in a row,
    so one noisy poll doesn't trip it.
    """
    if not references:
        return Comparison(Verdict.NEW)
    end = min([signature.duration, *(r.duration for r in references)])
    t = GRACE_S
    run: list[tuple[float, float, float, Verdict]] = []
    while t <= end + 1e-9:
        here, good = signature.current_at(t), reference_at(references, t)
        if abs(here - good) > max(TOLERANCE * abs(good), FLOOR_A):
            run.append((t, here, good, Verdict.LOW if here < good else Verdict.HIGH))
            if len(run) >= 2 and run[-1][3] is run[-2][3]:
                at, current, reference, verdict = run[-2]
                return Comparison(verdict, at, current, reference)
        else:
            run = []
        t += STEP_S
    return Comparison(Verdict.OK)
