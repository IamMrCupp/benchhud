"""Lining the thermal up finely (#318): rotation doesn't zoom, small steps, no stray wheel."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from benchhud.config import Config
from benchhud.overlay.calibration import Pair, solve
from benchhud.overlay.config_adapter import config_from_transform, transform_from_config
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.panels.controls import ControlsPanel

THERMAL = (256, 192)
PORTRAIT = (192, 256)  # the bench's T256s
SCOPE = (1920, 1080)


def _width(t: RegistrationTransform, src: tuple[int, int]) -> float:
    """How long the thermal's top edge is in the scope frame."""
    (x0, y0), (x1, y1) = t.map_point(0, 0, src, SCOPE), t.map_point(src[0], 0, src, SCOPE)
    return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5


@pytest.mark.parametrize("src, base", [(THERMAL, 0.0), (PORTRAIT, 90.0), (PORTRAIT, -90.0)])
@pytest.mark.parametrize("tweak", [-5.0, -0.5, 0.1, 2.0, 5.0])
def test_fine_rotation_does_not_change_the_size(src, base, tweak) -> None:
    straight = RegistrationTransform(scale=1.2, rotation_deg=base)
    turned = RegistrationTransform(scale=1.2, rotation_deg=base + tweak)
    assert _width(turned, src) == pytest.approx(_width(straight, src))


def test_a_quarter_turn_still_fills_the_frame() -> None:
    assert RegistrationTransform(rotation_deg=88.0).fit_scale(PORTRAIT, SCOPE) == pytest.approx(
        RegistrationTransform.cover(PORTRAIT, SCOPE, 90.0)
    )
    assert RegistrationTransform.nearest_quarter(-93.0) == -90.0
    assert RegistrationTransform.nearest_quarter(179.0) == 180.0


@pytest.mark.parametrize("rotation", [0.0, 3.0, 88.5, -92.0, 179.0])
def test_a_259_transform_converts_without_moving_a_pixel(rotation) -> None:
    old = RegistrationTransform(
        scale=1.3, offset_x=12, offset_y=-8, rotation_deg=rotation, quarter_fit=False
    )
    new = old.migrated(PORTRAIT, SCOPE)
    assert new.current
    for point in [(0, 0), (192, 0), (96, 128), (40, 200)]:
        assert new.map_point(*point, PORTRAIT, SCOPE) == pytest.approx(
            old.map_point(*point, PORTRAIT, SCOPE)
        )


def test_version_three_configs_are_the_259_fit() -> None:
    cfg = Config.from_dict({"registration": {"rotation_deg": 3.0, "version": 3}})
    t = transform_from_config(cfg.registration)
    assert t.fit and t.rotated_fit and not t.quarter_fit and not t.current
    assert config_from_transform(t.migrated(THERMAL, SCOPE)).version == 4


def test_calibration_solves_a_small_tilt() -> None:
    true = RegistrationTransform(scale=1.237, offset_x=31.4, offset_y=-18.6, rotation_deg=-3.3)
    p1, p2 = (20.0, 30.0), (230.0, 170.0)
    got = solve(
        Pair(p1, true.map_point(*p1, THERMAL, SCOPE), 300),
        Pair(p2, true.map_point(*p2, THERMAL, SCOPE), 300),
        THERMAL,
        SCOPE,
    )
    assert got.current
    assert got.scale == pytest.approx(1.237)
    assert got.rotation_deg == pytest.approx(-3.3)
    assert (got.offset_x, got.offset_y) == pytest.approx((31.4, -18.6))


def test_scale_and_rotation_step_finely(qtbot) -> None:
    panel = ControlsPanel(Config())
    qtbot.addWidget(panel)
    assert panel.scale_spin.singleStep() == pytest.approx(0.005)
    assert panel.scale_spin.decimals() == 3
    assert panel.rotation_spin.singleStep() == pytest.approx(0.1)
    panel.scale_spin.stepUp()
    assert panel.current_transform().scale == pytest.approx(1.005)


def test_a_calibrated_transform_keeps_full_precision(qtbot) -> None:
    config = Config()
    panel = ControlsPanel(config)
    qtbot.addWidget(panel)
    precise = RegistrationTransform(
        scale=1.23456789, offset_x=10.123456, offset_y=-4.98765, rotation_deg=1.23456
    )
    panel.set_transform(precise)
    assert panel.current_transform() == precise
    assert config.registration.scale == pytest.approx(1.23456789, abs=1e-9)
    # Changing a control drops the hidden precision: what's shown is the value.
    panel.offset_x_spin.setValue(20.0)
    t = panel.current_transform()
    assert t.offset_x == 20.0 and t.scale == pytest.approx(1.235)
    assert config.registration.scale == pytest.approx(1.235)


def _wheel(widget) -> None:
    centre = QPointF(widget.rect().center())
    event = QWheelEvent(
        centre,
        QPointF(widget.mapToGlobal(centre.toPoint())),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def test_the_wheel_only_turns_a_focused_spin_box(qtbot, monkeypatch) -> None:
    panel = ControlsPanel(Config())
    qtbot.addWidget(panel)
    spin = panel.scale_spin
    assert spin.focusPolicy() == Qt.FocusPolicy.StrongFocus  # the wheel can't focus it
    _wheel(spin)
    assert spin.value() == pytest.approx(1.0)  # scrolled past: untouched
    # Offscreen Qt has no active window to give focus to, so stand in for a click.
    monkeypatch.setattr(spin, "hasFocus", lambda: True)
    _wheel(spin)
    assert spin.value() == pytest.approx(1.005)
