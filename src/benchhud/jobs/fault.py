"""Detect over-current fault events from the live meter.

Powering a shorted board spikes the current; capturing that in the job timeline
automatically means the repair log records the fault without the tech reaching
for a button mid-measurement.

:class:`FaultMonitor` is a pure edge detector — no Qt, no store — so the tricky
bit (fire once per short, not once per frame) is exhaustively testable. Wiring
it to a job lives in the shell.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultEvent:
    """A single over-current crossing."""

    current: float
    threshold: float


class FaultMonitor:
    """Rising-edge over-current detector with hysteresis re-arm.

    Fires the moment current crosses above ``threshold``; then stays quiet until
    it falls back below ``threshold - hysteresis``. So a board that's powered,
    shorts, and is powered off yields exactly one event — not one per poll while
    the current sits high, and no chatter from noise around the threshold.
    """

    def __init__(self, threshold: float, *, hysteresis: float = 0.05) -> None:
        self.threshold = threshold
        self._hysteresis = max(hysteresis, 0.0)
        self._armed = True

    def observe(self, current: float) -> FaultEvent | None:
        """Feed one current reading; return a :class:`FaultEvent` on a fresh trip."""
        if self.threshold <= 0:
            return None  # a non-positive threshold disables detection
        if self._armed and current > self.threshold:
            self._armed = False
            return FaultEvent(current=current, threshold=self.threshold)
        if not self._armed and current <= self.threshold - self._hysteresis:
            self._armed = True
        return None

    def reset(self) -> None:
        """Re-arm without waiting for the current to fall (e.g. after a config change)."""
        self._armed = True


def format_fault_note(event: FaultEvent) -> str:
    """The timeline note text for a fault event."""
    return f"⚡ Over-current fault: {event.current:.2f} A (threshold {event.threshold:.2f} A)"
