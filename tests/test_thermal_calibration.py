"""Two-point thermal calibration with a hot tip (#243)."""

from __future__ import annotations

import random

import numpy as np
import pytest
from PySide6.QtCore import QPointF

from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.overlay.calibration import CalibrationError, Pair, find_tip, solve
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.composite_view import CompositeView
from benchhud.shell.main_window import MainWindow

SRC = (256, 192)
DST = (1280, 720)


def frame_with_tip(x: int, y: int, ambient: float = 24.0, tip: float = 320.0) -> np.ndarray:
    temps = np.full((SRC[1], SRC[0]), ambient, dtype=np.float32)
    temps[y, x] = tip
    return temps


# --- the solver ---------------------------------------------------------------------


def test_solve_recovers_any_transform() -> None:
    rng = random.Random(243)
    for _ in range(100):
        true = RegistrationTransform(
            scale=rng.uniform(0.5, 8),
            offset_x=rng.uniform(-300, 300),
            offset_y=rng.uniform(-300, 300),
            rotation_deg=rng.uniform(-30, 30),
        )
        p1, p2 = (20.0, 30.0), (230.0, 170.0)
        got = solve(
            Pair(p1, true.map_point(*p1, SRC, DST), 300),
            Pair(p2, true.map_point(*p2, SRC, DST), 300),
            SRC,
            DST,
        )
        assert got.fit
        assert got.scale == pytest.approx(true.scale)
        assert got.rotation_deg == pytest.approx(true.rotation_deg)
        assert (got.offset_x, got.offset_y) == pytest.approx((true.offset_x, true.offset_y))


def test_points_too_close_are_refused() -> None:
    with pytest.raises(CalibrationError, match="too close"):
        solve(Pair((100, 100), (10, 10), 300), Pair((105, 100), (400, 300), 300), SRC, DST)


def test_the_same_click_twice_is_refused() -> None:
    with pytest.raises(CalibrationError, match="same place"):
        solve(Pair((20, 20), (500, 300), 300), Pair((200, 150), (500, 300), 300), SRC, DST)


def test_find_tip() -> None:
    assert find_tip(frame_with_tip(40, 50)) == ((40.0, 50.0), 320.0)


def test_a_cold_frame_has_no_tip() -> None:
    with pytest.raises(CalibrationError, match="Touch the iron tip"):
        find_tip(frame_with_tip(40, 50, tip=26.0))


# --- clicks on the picture ----------------------------------------------------------


def test_clicks_map_to_frame_pixels(qtbot) -> None:
    view = CompositeView()
    qtbot.addWidget(view)
    view.resize(640, 480)  # the 1280x720 frame shows at 640x360, centred: 60 px bars
    view.show()
    qtbot.waitExposed(view)
    view.show_frame(np.zeros((720, 1280, 3), dtype=np.uint8))
    shown = view._label.pixmap()
    top = (view._label.height() - shown.height()) / 2
    left = (view._label.width() - shown.width()) / 2
    x, y = view.frame_point(QPointF(left + shown.width() / 2, top + shown.height() / 4))
    assert x == pytest.approx(640, abs=3) and y == pytest.approx(180, abs=3)
    assert view.frame_point(QPointF(5, 1)) is None  # the bar above the picture


# --- the flow in the window -----------------------------------------------------------


@pytest.fixture
def window(qtbot):
    window = MainWindow(Config(), job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    window.scope_panel.update_frame(np.zeros((DST[1], DST[0], 3), dtype=np.uint8))
    return window


def _touch(window, thermal_xy, scope_xy) -> None:
    window.thermal_panel.update_temps(frame_with_tip(*thermal_xy))
    window.calibration_click(*scope_xy)


def test_needs_both_cameras(qtbot) -> None:
    window = MainWindow(Config(), job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    window.start_calibration()
    assert not window.calibrating
    assert "both cameras" in window.controls_panel.calibration_status.text()


def test_two_touches_calibrate_and_undo_goes_back(window) -> None:
    window.thermal_panel.update_temps(frame_with_tip(10, 10))
    before = window.controls_panel.current_transform()
    true = RegistrationTransform(scale=4.0, offset_x=-50, offset_y=30, rotation_deg=3.0)
    window.start_calibration()
    assert window.calibrating and "1 of 2" in window.controls_panel.calibration_status.text()
    _touch(window, (30, 40), true.map_point(30, 40, SRC, DST))
    assert "2 of 2" in window.controls_panel.calibration_status.text()
    _touch(window, (220, 160), true.map_point(220, 160, SRC, DST))

    got = window.controls_panel.current_transform()
    assert not window.calibrating
    assert got.scale == pytest.approx(4.0, abs=0.01)
    assert got.rotation_deg == pytest.approx(3.0, abs=0.05)
    assert window._config.registration.scale == pytest.approx(got.scale, abs=0.01)
    assert "Calibrated" in window.controls_panel.calibration_status.text()

    window.undo_calibration()
    assert window.controls_panel.current_transform() == before
    assert not window.controls_panel.undo_button.isEnabled()


def test_a_cold_touch_keeps_waiting(window) -> None:
    window.thermal_panel.update_temps(frame_with_tip(10, 10))
    window.start_calibration()
    window.thermal_panel.update_temps(frame_with_tip(30, 40, tip=25.0))
    window.calibration_click(100, 100)
    assert window.calibrating and window._calibration == []
    assert "Touch the iron tip" in window.controls_panel.calibration_status.text()


def test_a_bad_second_spot_keeps_the_first(window) -> None:
    window.thermal_panel.update_temps(frame_with_tip(10, 10))
    window.start_calibration()
    _touch(window, (100, 100), (300, 300))
    _touch(window, (102, 100), (900, 500))  # too close in the thermal image
    assert window.calibrating and len(window._calibration) == 1
    assert "too close" in window.controls_panel.calibration_status.text()


def test_escape_cancels(window) -> None:
    window.thermal_panel.update_temps(frame_with_tip(10, 10))
    before = window.controls_panel.current_transform()
    window.start_calibration()
    _touch(window, (30, 40), (100, 100))
    window.cancel_calibration()
    assert not window.calibrating
    assert window.controls_panel.current_transform() == before
    window.calibration_click(5, 5)  # clicks do nothing once it's over
    assert window.controls_panel.current_transform() == before
