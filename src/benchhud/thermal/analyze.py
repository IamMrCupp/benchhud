"""Thermal analysis primitives.

These operate on a per-pixel temperature array (``float``, degrees Celsius),
shape ``(H, W)`` — whatever the Y16→°C conversion produces on the bench. They are
independent of capture and of the exact scaling, so they're fully unit-tested
against synthetic arrays here. The overlay (Phase 4) and thermal panel consume
them to highlight the hottest component and threshold-tint hot regions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Hotspot:
    """The hottest pixel: ``value`` °C at column ``x``, row ``y``."""

    value: float
    x: int
    y: int


@dataclass(frozen=True)
class TemperatureStats:
    min: float
    max: float
    mean: float


def _validate(temps: np.ndarray) -> None:
    if temps.ndim != 2:
        raise ValueError("temperature array must be 2-D (H, W)")
    if temps.size == 0:
        raise ValueError("temperature array is empty")


def hottest(temps: np.ndarray) -> Hotspot:
    """Return the hottest pixel and its location (first one, in row-major order)."""
    _validate(temps)
    y, x = np.unravel_index(int(np.argmax(temps)), temps.shape)
    return Hotspot(value=float(temps[y, x]), x=int(x), y=int(y))


def threshold_mask(temps: np.ndarray, threshold: float) -> np.ndarray:
    """Boolean mask of pixels at or above ``threshold`` °C."""
    _validate(temps)
    return temps >= threshold


def stats(temps: np.ndarray) -> TemperatureStats:
    """Min / max / mean temperature over the frame."""
    _validate(temps)
    return TemperatureStats(
        min=float(temps.min()),
        max=float(temps.max()),
        mean=float(temps.mean()),
    )
