"""A portrait thermal on a landscape scope (#259): quarter turns and a fit that follows."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.config import Config
from benchhud.overlay.calibration import Pair, solve
from benchhud.overlay.config_adapter import config_from_transform, transform_from_config
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.panels.controls import ControlsPanel

PORTRAIT = (192, 256)  # the bench's T256s
SCOPE = (1280, 720)


def test_turned_portrait_fills_a_landscape_frame_at_scale_one() -> None:
    turned = RegistrationTransform(rotation_deg=90.0)
    temps = np.full((PORTRAIT[1], PORTRAIT[0]), 30.0, dtype=np.float32)
    out = turned.warp(temps, SCOPE)
    # No empty border (bar the sub-pixel edge sliver bilinear sampling leaves).
    assert out[6:-6, 6:-6].min() == pytest.approx(30.0)
    # The long side (256) now runs across the 1280 width: 5x.
    assert RegistrationTransform.cover(PORTRAIT, SCOPE, 90.0) == pytest.approx(5.0)
    assert RegistrationTransform.cover(PORTRAIT, SCOPE, 0.0) == pytest.approx(1280 / 192)


def test_turned_wraps_and_keeps_the_rest() -> None:
    t = RegistrationTransform(scale=2.0, offset_x=5.0, rotation_deg=170.0)
    left = t.turned(1)
    assert left.rotation_deg == pytest.approx(-100.0) and left.scale == 2.0
    assert t.turned(-1).rotation_deg == pytest.approx(80.0)
    assert t.turned(4).rotation_deg == pytest.approx(170.0)


def test_a_242_transform_converts_without_moving_a_pixel() -> None:
    old = RegistrationTransform(
        scale=1.3, offset_x=12, offset_y=-8, rotation_deg=90.0, rotated_fit=False
    )
    new = old.migrated(PORTRAIT, SCOPE)
    assert new.current and new is not old
    for point in [(0, 0), (192, 0), (96, 128), (40, 200)]:
        assert new.map_point(*point, PORTRAIT, SCOPE) == pytest.approx(
            old.map_point(*point, PORTRAIT, SCOPE)
        )


def test_version_two_configs_are_the_242_fit() -> None:
    cfg = Config.from_dict({"registration": {"rotation_deg": 90.0, "version": 2}})
    t = transform_from_config(cfg.registration)
    assert t.fit and not t.rotated_fit and not t.current
    assert config_from_transform(t.migrated(PORTRAIT, SCOPE)).version == 4


def test_calibration_solves_a_quarter_turn() -> None:
    true = RegistrationTransform(scale=1.4, offset_x=30, offset_y=-20, rotation_deg=-90.0)
    p1, p2 = (20.0, 30.0), (170.0, 230.0)
    got = solve(
        Pair(p1, true.map_point(*p1, PORTRAIT, SCOPE), 300),
        Pair(p2, true.map_point(*p2, PORTRAIT, SCOPE), 300),
        PORTRAIT,
        SCOPE,
    )
    assert got.rotation_deg == pytest.approx(-90.0)
    assert got.scale == pytest.approx(1.4)
    assert (got.offset_x, got.offset_y) == pytest.approx((30, -20))


def test_the_buttons_turn_and_save(qtbot) -> None:
    config = Config()
    panel = ControlsPanel(config)
    qtbot.addWidget(panel)
    panel.turn_left_button.click()
    assert panel.rotation_spin.value() == pytest.approx(90.0)
    assert config.registration.rotation_deg == pytest.approx(90.0)
    panel.turn_right_button.click()
    panel.turn_right_button.click()
    assert panel.rotation_spin.value() == pytest.approx(-90.0)
