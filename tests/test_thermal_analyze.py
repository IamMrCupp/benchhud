"""Tests for thermal analysis primitives."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.thermal.analyze import hottest, stats, threshold_mask


def test_hottest_finds_peak_and_location() -> None:
    temps = np.full((10, 12), 25.0, dtype=np.float32)
    temps[3, 7] = 88.5  # (row=3, col=7)
    spot = hottest(temps)
    assert spot.value == pytest.approx(88.5)
    assert spot.x == 7
    assert spot.y == 3


def test_threshold_mask_selects_hot_pixels() -> None:
    temps = np.array([[20.0, 60.0], [61.0, 10.0]], dtype=np.float32)
    mask = threshold_mask(temps, 60.0)
    assert mask.tolist() == [[False, True], [True, False]]
    assert int(mask.sum()) == 2


def test_stats() -> None:
    temps = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)
    s = stats(temps)
    assert s.min == pytest.approx(0.0)
    assert s.max == pytest.approx(30.0)
    assert s.mean == pytest.approx(15.0)


def test_validation_rejects_1d() -> None:
    with pytest.raises(ValueError, match="2-D"):
        hottest(np.zeros(5, dtype=np.float32))


def test_validation_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        stats(np.zeros((0, 0), dtype=np.float32))
