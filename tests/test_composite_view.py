"""The central composite view and the window's compose path."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.shell.composite_view import CompositeView
from benchhud.shell.main_window import MainWindow


def _scope(width: int = 64, height: int = 48) -> np.ndarray:
    return np.full((height, width, 3), 40, dtype=np.uint8)


def _temps(width: int = 32, height: int = 24) -> np.ndarray:
    return np.linspace(20.0, 90.0, width * height, dtype=np.float32).reshape(height, width)


def test_view_starts_empty_and_accepts_a_frame(qtbot):
    view = CompositeView()
    qtbot.addWidget(view)
    assert not view.has_frame
    view.show_frame(_scope())
    assert view.has_frame


def test_view_ignores_none(qtbot):
    view = CompositeView()
    qtbot.addWidget(view)
    view.show_frame(None)
    assert not view.has_frame


def test_view_survives_resize(qtbot):
    view = CompositeView()
    qtbot.addWidget(view)
    view.show_frame(_scope())
    view.resize(300, 200)
    assert view.has_frame


@pytest.fixture
def window(qtbot, tmp_path):
    from benchhud.jobs.store import JobStore

    win = MainWindow(job_store=JobStore(tmp_path / "jobs.db"))
    qtbot.addWidget(win)
    return win


def test_no_feeds_leaves_the_placeholder(window):
    window.render_composite()
    assert not window.composite_view.has_frame


def test_scope_only_renders(window):
    window.scope_panel.update_frame(_scope())
    window.render_composite()
    assert window.composite_view.has_frame


def test_thermal_only_renders(window):
    window.thermal_panel.update_temps(_temps())
    window.render_composite()
    assert window.composite_view.has_frame


def test_both_feeds_compose(window):
    window.scope_panel.update_frame(_scope())
    window.thermal_panel.update_temps(_temps())
    window.render_composite()
    assert window.composite_view.has_frame


def test_a_control_change_re_renders(window):
    """The overlay controls drive the central view — not just the config file."""
    window.scope_panel.update_frame(_scope())
    window.thermal_panel.update_temps(_temps())
    window.controls_panel.alpha_slider.setValue(90)
    assert window.composite_view.has_frame


def test_demo_mode_fills_the_centre(window):
    window.set_demo_mode(True)
    window._demo_tick()
    assert window.composite_view.has_frame
    window.set_demo_mode(False)
