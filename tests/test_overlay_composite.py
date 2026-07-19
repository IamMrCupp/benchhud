"""Tests for the compositor (hardware-free)."""

from __future__ import annotations

import numpy as np

from benchhud.overlay.composite import (
    OverlayMode,
    OverlaySettings,
    blend,
    colorize,
    compose,
    draw_marker,
    tint_above,
)
from benchhud.overlay.transform import RegistrationTransform


def _scope(width: int = 40, height: int = 30) -> np.ndarray:
    return np.full((height, width, 3), 50, dtype=np.uint8)


def test_colorize_shape_and_uniformity() -> None:
    temps = np.full((10, 12), 60.0, dtype=np.float32)  # constant -> uniform color
    heat = colorize(temps, 20.0, 100.0)
    assert heat.shape == (10, 12, 3)
    assert heat.dtype == np.uint8
    assert np.all(heat == heat[0, 0])


def test_colorize_degenerate_range() -> None:
    temps = np.full((4, 4), 30.0, dtype=np.float32)
    heat = colorize(temps, 50.0, 50.0)  # tmax <= tmin
    assert heat.shape == (4, 4, 3)
    assert np.all(heat == 0)


def test_blend_extremes() -> None:
    base = _scope()
    overlay = np.full_like(base, 200)
    assert np.allclose(blend(base, overlay, 0.0), base)
    assert np.allclose(blend(base, overlay, 1.0), overlay)


def test_tint_above_only_touches_hot_pixels() -> None:
    scope = _scope(4, 4)
    temps = np.zeros((4, 4), dtype=np.float32)
    temps[0, 0] = 80.0  # single hot pixel
    out = tint_above(scope, temps, threshold=60.0, color=(0, 0, 255), alpha=1.0)
    assert tuple(out[0, 0]) == (0, 0, 255)  # tinted to red
    assert tuple(out[3, 3]) == (50, 50, 50)  # untouched


def test_draw_marker_modifies_image() -> None:
    scope = _scope()
    out = draw_marker(scope, 20, 15, label="42.0C")
    assert out.shape == scope.shape
    assert not np.array_equal(out, scope)  # something was drawn


def test_compose_blend_alpha_zero_returns_scope() -> None:
    scope = _scope()
    temps = np.full((15, 20), 70.0, dtype=np.float32)
    settings = OverlaySettings(mode=OverlayMode.BLEND, alpha=0.0)
    out = compose(scope, temps, RegistrationTransform(), settings)
    assert np.allclose(out, scope)


def test_compose_each_mode_returns_scope_sized_bgr() -> None:
    scope = _scope(40, 30)
    temps = np.zeros((15, 20), dtype=np.float32)
    temps[5, 5] = 90.0
    transform = RegistrationTransform(scale=2.0)
    for mode in OverlayMode:
        out = compose(scope, temps, transform, OverlaySettings(mode=mode))
        assert out.shape == (30, 40, 3)
        assert out.dtype == np.uint8
