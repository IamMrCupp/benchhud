"""Composing at the size that's shown, not the scope's full frame (#348)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from benchhud.config import Config
from benchhud.output.sink import FakeSink
from benchhud.overlay.composite import OverlayMode, OverlaySettings, compose, heat_blend
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.main_window import MainWindow

THERMAL = (256, 192)  # (width, height)
FULL = (1920, 1080)


def _placed() -> RegistrationTransform:
    # Like the bench's: zoomed, turned a little, and well off centre.
    return RegistrationTransform(scale=3.85, offset_x=-300.0, offset_y=-600.0, rotation_deg=4.0)


@pytest.mark.parametrize("k", [2 / 3, 0.5, 0.25])
def test_a_scaled_frame_puts_every_thermal_point_in_the_same_place(k) -> None:
    transform = _placed()
    small = (round(FULL[0] * k), round(FULL[1] * k))
    for point in ((0, 0), (128, 96), (255, 191), (40, 170)):
        big = transform.map_point(*point, THERMAL, FULL)
        scaled = transform.for_scaled_frame(k).map_point(*point, THERMAL, small)
        assert scaled == pytest.approx((big[0] * k, big[1] * k), abs=1e-6)


def test_scale_one_is_the_same_transform() -> None:
    transform = _placed()
    assert transform.for_scaled_frame(1.0) is transform


def test_the_overlay_lands_on_the_same_spot_at_the_smaller_size() -> None:
    scope = np.zeros((1080, 1920, 3), dtype=np.uint8)
    temps = np.full((192, 256), 25.0, dtype=np.float32)
    cv2.circle(temps, (150, 150), 6, 90.0, -1)
    transform = RegistrationTransform(scale=1.5, offset_x=120.0, offset_y=-80.0)
    settings = OverlaySettings(
        mode=OverlayMode.BLEND, alpha=1.0, tmin=25.0, tmax=90.0, scale_bar=False
    )

    def centre(frame):
        ys, xs = np.nonzero(frame.max(axis=2) > 40)
        return xs.mean(), ys.mean()

    full = centre(compose(scope, temps, transform, settings))
    k = 2 / 3
    small_scope = cv2.resize(scope, (1280, 720))
    small = centre(compose(small_scope, temps, transform.for_scaled_frame(k), settings))
    assert small == pytest.approx((full[0] * k, full[1] * k), abs=1.5)


def test_the_faster_blend_matches_the_old_one_to_a_level() -> None:
    rng = np.random.default_rng(3)
    scope = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)
    temps = rng.uniform(20, 60, (60, 80)).astype(np.float32)
    tmin, tmax, alpha = 25.0, 55.0, 0.6
    weight = (np.clip((temps - tmin) / (tmax - tmin), 0.0, 1.0) * alpha)[..., None]
    heat = cv2.applyColorMap(
        (np.clip((temps - tmin) / (tmax - tmin), 0, 1) * 255).astype(np.uint8),
        cv2.COLORMAP_INFERNO,
    ).astype(np.float32)
    old = (scope.astype(np.float32) * (1.0 - weight) + heat * weight).astype(np.uint8)
    new = heat_blend(scope, temps, tmin, tmax, cv2.COLORMAP_INFERNO, alpha)
    assert np.abs(new.astype(int) - old.astype(int)).max() <= 1


def _window(qtbot, sink_size=None) -> tuple[MainWindow, list]:
    config = Config()
    if sink_size:
        config.output.enabled = True
        config.output.width, config.output.height = sink_size
    window = MainWindow(config)
    qtbot.addWidget(window)
    if sink_size:
        window.start_output(lambda d, w, h: FakeSink(w, h))
    shown: list = []
    window._show_composite = lambda frame, *a: shown.append(frame)
    return window, shown


def _scope() -> np.ndarray:
    return np.zeros((1080, 1920, 3), dtype=np.uint8)


def test_the_scope_is_composed_at_the_output_size(qtbot) -> None:
    window, shown = _window(qtbot, sink_size=(1280, 720))
    window.scope_panel.update_frame(_scope())
    window.render_composite()
    assert shown[-1].shape == (720, 1280, 3)
    assert window._compose_scale == pytest.approx(2 / 3)
    assert window.scope_panel.last_frame.shape == (1080, 1920, 3)  # snapshots stay full size


def test_nothing_to_size_for_leaves_the_frame_alone(qtbot) -> None:
    window, shown = _window(qtbot)  # no output, view not shown
    window.scope_panel.update_frame(_scope())
    window.render_composite()
    assert shown[-1].shape == (1080, 1920, 3) and window._compose_scale == 1.0


def test_a_frame_is_never_scaled_up_or_shaved(qtbot) -> None:
    window, shown = _window(qtbot, sink_size=(1280, 720))
    window.scope_panel.update_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
    window.render_composite()
    assert shown[-1].shape == (720, 1280, 3) and window._compose_scale == 1.0
    window.scope_panel.update_frame(np.zeros((760, 1350, 3), dtype=np.uint8))  # 5% over
    window.render_composite()
    assert shown[-1].shape == (760, 1350, 3)


def test_the_thermal_is_registered_on_the_smaller_frame(qtbot) -> None:
    window, _shown = _window(qtbot, sink_size=(1280, 720))
    window.controls_panel.set_transform(_placed())
    temps = np.full((192, 256), 25.0, dtype=np.float32)
    window.thermal_panel.update_temps(temps)
    window.scope_panel.update_frame(_scope())
    seen = {}

    def spy(scope, temps, transform, settings, marker=None):
        seen.update(shape=scope.shape, transform=transform)
        return scope

    import benchhud.shell.main_window as mw

    original, mw.compose = mw.compose, spy
    try:
        window.render_composite()
    finally:
        mw.compose = original
    assert seen["shape"] == (720, 1280, 3)
    assert seen["transform"].offset_x == pytest.approx(-200.0)
    assert seen["transform"].offset_y == pytest.approx(-400.0)
    assert seen["transform"].scale == pytest.approx(3.85)
    assert window.controls_panel.current_transform().offset_x == pytest.approx(
        -300.0
    )  # saved as is


def test_a_calibration_click_is_in_the_scopes_own_pixels(qtbot, monkeypatch) -> None:
    import benchhud.shell.main_window as mw

    window, _shown = _window(qtbot, sink_size=(1280, 720))
    window.thermal_panel.update_temps(np.full((192, 256), 25.0, dtype=np.float32))
    window.scope_panel.update_frame(_scope())
    window.render_composite()
    monkeypatch.setattr(mw, "find_tip", lambda _temps: ((10.0, 20.0), 300.0))
    window._calibration = []
    window.calibration_click(640.0, 360.0)  # the middle of the 720p composite
    assert window._calibration[0].scope == pytest.approx((960.0, 540.0))  # the scope's middle
