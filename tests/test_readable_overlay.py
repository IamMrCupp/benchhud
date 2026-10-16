"""Readable thermal overlays (#244): heat-only blend, auto range, outlines, scale."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from benchhud.config import Config
from benchhud.overlay.composite import (
    MIN_SPAN_C,
    OverlayMode,
    OverlaySettings,
    RangeTracker,
    auto_range,
    compose,
    draw_scale_bar,
    heat_blend,
)
from benchhud.overlay.config_adapter import config_from_settings, settings_from_config
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.panels.controls import ControlsPanel


def board(width=320, height=240) -> np.ndarray:
    """A green board with a stripe pattern, so 'untouched' is checkable."""
    scope = np.zeros((height, width, 3), dtype=np.uint8)
    scope[:, :] = (40, 120, 40)
    scope[::8, :] = (200, 200, 200)
    return scope


def warm_spot(width=320, height=240, ambient=28.0, peak=70.0) -> np.ndarray:
    temps = np.full((height, width), ambient, dtype=np.float32)
    cv2.circle(temps, (240, 60), 20, float(peak), -1)
    return temps


def test_cool_areas_are_left_alone() -> None:
    scope, temps = board(), warm_spot()
    out = heat_blend(scope, temps, 28.0, 70.0, cv2.COLORMAP_INFERNO, 0.6)
    assert np.array_equal(out[200:, :100], scope[200:, :100])  # ambient: untouched
    assert not np.array_equal(out[60, 240], scope[60, 240])  # the hot spot: coloured


def test_the_old_whole_frame_dimming_is_gone() -> None:
    """The bench complaint: a near-ambient board came out darker everywhere."""
    scope, temps = board(), warm_spot()
    settings = OverlaySettings(mode=OverlayMode.BLEND, alpha=0.5, tmin=28.0, tmax=70.0)
    out = compose(scope, temps, RegistrationTransform(), settings)
    cool = (slice(150, 240), slice(150, 320))
    assert out[cool].mean() == pytest.approx(scope[cool].mean(), abs=0.5)


def test_auto_range_is_ambient_to_peak() -> None:
    assert auto_range(warm_spot()) == pytest.approx((28.0, 70.0))
    flat = np.full((10, 10), 25.0, dtype=np.float32)
    assert auto_range(flat) == (25.0, 25.0 + MIN_SPAN_C)  # never a zero-width range


def test_the_tracker_rises_at_once_and_settles_slowly() -> None:
    tracker = RangeTracker(smoothing=0.5)
    assert tracker.update(warm_spot(peak=60.0)) == pytest.approx((28.0, 60.0))
    assert tracker.update(warm_spot(peak=90.0))[1] == pytest.approx(90.0)  # no clipping
    assert tracker.update(warm_spot(peak=40.0))[1] == pytest.approx(65.0)  # halfway down


def test_outline_mode_draws_isotherms_on_a_clear_board() -> None:
    scope, temps = board(), warm_spot()
    settings = OverlaySettings(mode=OverlayMode.OUTLINE, tmin=28.0, tmax=70.0, scale_bar=False)
    out = compose(scope, temps, RegistrationTransform(), settings)
    assert np.array_equal(out[150:, :150], scope[150:, :150])  # away from the spot: clear
    ring = out[60, 240 - 20 : 240 + 21]
    assert (ring != scope[60, 240 - 20 : 240 + 21]).any()  # an outline around it


def test_the_scale_bar_draws_and_skips_tiny_frames() -> None:
    frame = board(640, 480)
    out = draw_scale_bar(frame, 28.0, 70.0, cv2.COLORMAP_INFERNO)
    assert not np.array_equal(out[20:130, 16:30], frame[20:130, 16:30])
    tiny = board(40, 30)
    assert np.array_equal(draw_scale_bar(tiny, 28.0, 70.0, cv2.COLORMAP_INFERNO), tiny)


def test_settings_round_trip_and_defaults() -> None:
    assert Config().overlay.auto_range and Config().overlay.scale_bar
    old = Config.from_dict({"overlay": {"mode": "tint", "tmin": 20.0, "tmax": 100.0}})
    assert old.overlay.auto_range  # older configs get the readable default
    cfg = config_from_settings(
        OverlaySettings(mode=OverlayMode.OUTLINE, auto_range=False, scale_bar=False)
    )
    back = settings_from_config(cfg)
    assert back.mode is OverlayMode.OUTLINE and not back.auto_range and not back.scale_bar


def test_controls_show_the_manual_range_only_when_manual(qtbot) -> None:
    config = Config()
    panel = ControlsPanel(config)
    qtbot.addWidget(panel)
    panel.show()
    assert not panel._range_row.isVisible()
    panel.range_combo.setCurrentText("Manual")
    panel.tmin_spin.setValue(30.0)
    assert panel._range_row.isVisible()
    assert not config.overlay.auto_range and config.overlay.tmin == 30.0
    assert "outline" in [panel.mode_combo.itemText(i) for i in range(panel.mode_combo.count())]


def test_a_heat_map_range_starts_at_the_cool_end() -> None:
    """Starting at the median, half a flat board flickered black and colour (#278)."""
    rng = np.random.default_rng(0)
    temps = (30.0 + rng.normal(0.0, 0.5, (192, 256))).astype(np.float32)
    low, high = auto_range(temps, low_percentile=1.0)
    assert low < 29.0 and high - low >= MIN_SPAN_C
    # Frame-to-frame noise moves a pixel only a sliver of the palette.
    assert 1.0 / (high - low) < 0.15
