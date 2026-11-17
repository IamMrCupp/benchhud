"""The first-run layout gives the composite the room, not the side columns.

Qt sizes a dock to its size hint, and the left column's hint is its whole row
of tabs: at 1600 px wide it took some 950 px and left the composite a strip (#422).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.shell.main_window import METER_DOCK_WIDTH, SIDE_DOCK_WIDTH, MainWindow
from benchhud.shell.theme import apply_theme, stylesheet


@pytest.fixture
def window(qtbot, qapp):
    previous = qapp.styleSheet()
    apply_theme(qapp)
    config = Config()
    config.shell.window_width, config.shell.window_height = 1600, 900
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    window.resize(1600, 900)
    window.show()
    qtbot.waitExposed(window)
    yield window
    qapp.setStyleSheet(previous)


def test_side_columns_open_at_their_default_widths(window) -> None:
    assert window.panels.dock("controls").width() == SIDE_DOCK_WIDTH
    assert window.panels.dock("power").width() == METER_DOCK_WIDTH


def test_the_composite_gets_most_of_the_window(window) -> None:
    assert window.composite_view.width() > window.width() / 2


def test_reset_layout_puts_the_widths_back(window, qtbot) -> None:
    controls = window.panels.dock("controls")
    window.resizeDocks([controls], [700], Qt.Orientation.Horizontal)
    qtbot.waitUntil(lambda: controls.width() == 700)
    window.reset_layout()
    qtbot.waitUntil(lambda: controls.width() == SIDE_DOCK_WIDTH)


def test_every_colour_name_in_the_stylesheet_is_filled_in() -> None:
    assert "@" not in stylesheet()
