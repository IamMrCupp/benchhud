"""The left column's tabs split into groups, with Tools under the composite (#429)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QTabBar

from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.shell.dock_groups import (
    BOTTOM_DOCK_MAX,
    BOTTOM_DOCK_MIN,
    DOCK_GROUPS,
    Area,
    bottom_dock_height,
    group_of,
    heading,
)
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panel_manager import DockTitleBar
from benchhud.shell.theme import apply_theme

# --- the arrangement, as data ---------------------------------------------


def test_no_panel_is_in_two_groups() -> None:
    ids = [pid for group in DOCK_GROUPS for pid in group.panel_ids]
    assert len(ids) == len(set(ids))


def test_the_groups_follow_the_issue() -> None:
    groups = {g.title: g.panel_ids for g in DOCK_GROUPS}
    assert set(groups["Bench"]) == {"controls", "cameras", "jobs", "readings", "inventory"}
    assert set(groups["Tools"]) == {"launcher", "terminal", "flash", "minipro"}
    assert set(groups["Recon"]) == {"engagement", "interfaces", "pinout", "captures", "chip"}


def test_a_lower_group_sits_under_one_placed_before_it() -> None:
    seen: set[Area] = set()
    for group in DOCK_GROUPS:
        if group.area is Area.LEFT_LOWER:
            assert Area.LEFT in seen
        if group.area is Area.RIGHT_LOWER:
            assert Area.RIGHT in seen
        seen.add(group.area)


def test_group_of_finds_a_panel_and_misses_a_stranger() -> None:
    group = group_of("terminal")
    assert group is not None and group.title == "Tools"
    assert group_of("nope") is None


def test_heading_is_small_capitals() -> None:
    assert heading("Bench") == "BENCH"


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (920, 800, 800 - round(920 * 9 / 16)),  # the letterbox, inside the bounds
        (1600, 1000, BOTTOM_DOCK_MIN),  # wide centre, no letterbox: the floor
        (400, 1400, BOTTOM_DOCK_MAX),  # a tall, narrow centre: the cap
    ],
)
def test_bottom_strip_takes_the_letterbox_within_bounds(width, height, expected) -> None:
    assert abs(bottom_dock_height(width, height) - expected) <= 1


# --- the window -------------------------------------------------------------


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


def test_each_group_is_one_tab_row(window) -> None:
    for group in DOCK_GROUPS:
        first = window.panels.dock(group.panel_ids[0])
        tabbed = {d.objectName() for d in window.tabifiedDockWidgets(first)}
        expected = {f"dock__{pid}" for pid in group.panel_ids[1:]}
        assert tabbed == expected, group.title


def test_recon_sits_under_bench_in_the_left_column(window) -> None:
    bench, recon = window.panels.dock("controls"), window.panels.dock("engagement")
    left = Qt.DockWidgetArea.LeftDockWidgetArea
    assert window.dockWidgetArea(bench) == left
    assert window.dockWidgetArea(recon) == left
    assert recon.geometry().top() >= bench.geometry().bottom()
    assert bench.height() > recon.height()  # Bench is the bigger share


def test_tools_strip_sits_under_the_composite_only(window) -> None:
    tools = window.panels.dock("launcher")
    assert window.dockWidgetArea(tools) == Qt.DockWidgetArea.BottomDockWidgetArea
    # Between the side columns, which keep the window's full height.
    assert tools.geometry().left() >= window.panels.dock("controls").geometry().right()
    assert tools.geometry().right() <= window.panels.dock("power").geometry().left()
    assert tools.geometry().top() >= window.composite_view.geometry().bottom()


def test_the_strip_costs_the_picture_nothing(window) -> None:
    """The composite is width-bound: a 16:9 picture still fits its full width."""
    view = window.composite_view
    assert view.height() + 1 >= view.width() * 9 / 16


def test_no_stray_tab_bar_paints_over_a_panel(window) -> None:
    """Tabifying back to back used to leave dead tab bars over Controls.

    One live bar per group that has more than one panel, and nothing else.
    """
    painting = [
        bar
        for bar in window.findChildren(QTabBar)
        if bar.parent() is window and bar.isVisible() and not bar.visibleRegion().isEmpty()
    ]
    expected = sum(1 for group in DOCK_GROUPS if len(group.panel_ids) > 1)
    assert len(painting) == expected


def test_every_dock_shows_its_group_heading(window) -> None:
    for group in DOCK_GROUPS:
        for pid in group.panel_ids:
            bar = window.panels.dock(pid).titleBarWidget()
            assert isinstance(bar, DockTitleBar)
            assert bar.text() == heading(group.title)


def test_the_view_menu_can_bring_back_every_panel(window) -> None:
    """A heading bar has no close button: the View menu is the way back."""
    menus = {m.title(): m for m in window.menuBar().findChildren(QMenu)}
    actions = set(menus["View"].actions())
    for pid in window.panels.panel_ids():
        assert window.panels.dock(pid).toggleViewAction() in actions, pid


def test_reset_layout_brings_back_the_groups(window, qtbot) -> None:
    terminal = window.panels.dock("terminal")
    window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, terminal)
    window.reset_layout()
    qtbot.waitUntil(
        lambda: window.dockWidgetArea(terminal) == Qt.DockWidgetArea.BottomDockWidgetArea
    )
