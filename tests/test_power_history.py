"""Tests for the power-reading rolling history."""

from __future__ import annotations

import pytest

from benchhud.instruments.power_history import PowerHistory, PowerSample


def test_power_is_voltage_times_current() -> None:
    assert PowerSample(t=0.0, voltage=12.0, current=1.5).power == 18.0


def test_add_returns_sample_with_power() -> None:
    history = PowerHistory()
    sample = history.add(voltage=5.0, current=2.0, t=1.0)
    assert sample.power == 10.0
    assert history.latest() is sample
    assert len(history) == 1


def test_samples_are_time_ordered() -> None:
    history = PowerHistory()
    history.add(1.0, 1.0, t=0.0)
    history.add(2.0, 1.0, t=1.0)
    ts = [s.t for s in history.samples()]
    assert ts == [0.0, 1.0]


def test_bounded_eviction() -> None:
    history = PowerHistory(max_samples=3)
    for i in range(5):
        history.add(voltage=float(i), current=1.0, t=float(i))
    assert len(history) == 3
    # Oldest two evicted; newest three remain.
    assert [s.t for s in history.samples()] == [2.0, 3.0, 4.0]


def test_clear() -> None:
    history = PowerHistory()
    history.add(1.0, 1.0, t=0.0)
    history.clear()
    assert len(history) == 0
    assert history.latest() is None


def test_invalid_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        PowerHistory(max_samples=0)
