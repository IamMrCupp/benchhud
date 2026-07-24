"""Tests for the over-current fault detector (pure — no Qt, no store)."""

from __future__ import annotations

from benchhud.jobs.fault import FaultMonitor, format_fault_note


def _trip_currents(monitor: FaultMonitor, readings: list[float]) -> list[float]:
    """The currents at which the monitor fired, across a sequence of readings."""
    fired = []
    for r in readings:
        event = monitor.observe(r)
        if event is not None:
            fired.append(event.current)
    return fired


def test_fires_on_rising_edge() -> None:
    monitor = FaultMonitor(1.0)
    assert monitor.observe(0.3) is None
    event = monitor.observe(2.5)
    assert event is not None
    assert event.current == 2.5
    assert event.threshold == 1.0


def test_fires_only_once_while_current_stays_high() -> None:
    """A short that keeps drawing current must not spam a note every poll."""
    monitor = FaultMonitor(1.0)
    fired = _trip_currents(monitor, [0.2, 2.0, 2.1, 3.0, 2.5])
    assert fired == [2.0]  # one event, at the crossing


def test_re_arms_after_current_falls_back() -> None:
    """Power off, power on again → a second, separate fault."""
    monitor = FaultMonitor(1.0)
    fired = _trip_currents(monitor, [2.0, 2.0, 0.1, 2.2])
    assert fired == [2.0, 2.2]


def test_hysteresis_prevents_chatter_near_threshold() -> None:
    """Noise dipping just below the threshold must not re-arm."""
    monitor = FaultMonitor(1.0, hysteresis=0.1)
    # trips at 1.5, dips to 0.95 (above 1.0 - 0.1 = 0.9, so still disarmed), back up
    fired = _trip_currents(monitor, [1.5, 0.95, 1.6])
    assert fired == [1.5]  # the 0.95 dip didn't re-arm, so 1.6 is not a new fault


def test_re_arms_below_hysteresis_band() -> None:
    monitor = FaultMonitor(1.0, hysteresis=0.1)
    fired = _trip_currents(monitor, [1.5, 0.85, 1.6])  # 0.85 < 0.9 → re-armed
    assert fired == [1.5, 1.6]


def test_exactly_at_threshold_does_not_fire() -> None:
    """Strictly greater-than — a reading equal to the threshold is not a fault."""
    monitor = FaultMonitor(1.0)
    assert monitor.observe(1.0) is None


def test_non_positive_threshold_disables() -> None:
    assert FaultMonitor(0.0).observe(999.0) is None
    assert FaultMonitor(-1.0).observe(999.0) is None


def test_reset_re_arms_immediately() -> None:
    monitor = FaultMonitor(1.0)
    assert monitor.observe(2.0) is not None
    assert monitor.observe(2.0) is None  # still high, disarmed
    monitor.reset()
    assert monitor.observe(2.0) is not None  # armed again without a dip


def test_note_text() -> None:
    from benchhud.jobs.fault import FaultEvent

    note = format_fault_note(FaultEvent(current=2.345, threshold=1.0))
    assert note == "⚡ Over-current fault: 2.35 A (threshold 1.00 A)"
