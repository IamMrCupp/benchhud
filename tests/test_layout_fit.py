"""Everything fits a narrow dock, and nothing is cut off (#230).

A QA pass on 0.4.0 found dock tabs elided to "Contr…" and "Termi…", the
Terminal panel scrolling sideways, "Output OFF" cut in half, and the job form's
buttons pushed off the edge, all in a left dock about 275 px wide. These pin
the fixes: each panel's real minimum width, and the tab bars' eliding.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTabBar

from benchhud.config import Config, ToolConfig
from benchhud.jobs.store import JobStore
from benchhud.launcher.catalog import is_command_line
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.supply_controls import SupplyControls
from benchhud.shell.panels.terminal import TerminalPanel
from benchhud.shell.theme import apply_theme

#: The narrowest dock the panels are built for, less a vertical scrollbar.
DOCK_WIDTH = 260 - 12
#: The meter's LCD cells are sized to their widest reading, so it gets more.
METER_WIDTH = 300


@pytest.fixture(autouse=True)
def themed(qapp):
    """Measure what people see: the app's theme sets the padding."""
    previous = qapp.styleSheet()
    apply_theme(qapp)
    yield
    qapp.setStyleSheet(previous)


@pytest.fixture
def window(qtbot):
    config = Config()
    config.tools = [
        ToolConfig("OpenBoardView", "openboardview"),
        ToolConfig("flashrom", "flashrom"),  # no terminal flag: from before #225
    ]
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    return window


def test_every_panel_fits_a_narrow_dock(window) -> None:
    too_wide = {}
    for panel_id in window.panels.panel_ids():
        width = window.panels.panel(panel_id).minimumSizeHint().width()
        limit = METER_WIDTH if panel_id == "power" else DOCK_WIDTH
        if width > limit:
            too_wide[panel_id] = width
    assert not too_wide, f"panels wider than their dock: {too_wide}"


def test_no_explicit_minimum_below_the_layouts(window) -> None:
    """An explicit minimum under the layout's lets the scroll area squeeze and clip."""
    for panel_id in window.panels.panel_ids():
        panel = window.panels.panel(panel_id)
        explicit = panel.minimumWidth()
        assert explicit == 0 or explicit >= panel.layout().minimumSize().width(), panel_id


def test_dock_tabs_show_whole_titles(window, qtbot) -> None:
    window.show()
    qtbot.waitExposed(window)
    bars = [b for b in window.findChildren(QTabBar) if b.parent() is window]
    assert bars, "the default layout tabs the left docks"
    for bar in bars:
        assert bar.elideMode() == Qt.TextElideMode.ElideNone
        assert bar.usesScrollButtons()


def test_the_supply_buttons_get_their_full_width(qtbot) -> None:
    supply = SupplyControls()
    qtbot.addWidget(supply)
    supply.resize(supply.minimumSizeHint())
    supply.show()
    qtbot.waitExposed(supply)
    for button in (supply.on_button, supply.off_button, supply.set_voltage_button):
        assert button.width() >= button.minimumSizeHint().width(), button.text()


def test_terminal_off_hides_what_it_cant_use(qtbot) -> None:
    panel = TerminalPanel(lambda parent: QLabel("view", parent))
    qtbot.addWidget(panel)
    panel.show()
    panel.show_message("off", attachable=False)
    assert not panel.target_combo.isVisible() and not panel.restart_button.isVisible()
    panel.show_message("port busy")  # a serial problem: the picker must stay
    assert panel.target_combo.isVisible()


def test_tools_from_before_the_flag_are_still_terminal_tools(window) -> None:
    assert is_command_line("flashrom") and is_command_line("/usr/local/bin/minipro -l")
    assert not is_command_line("openboardview") and not is_command_line("")
    assert ">_" in window.launcher_panel.buttons["flashrom"].text()
    assert ">_" not in window.launcher_panel.buttons["OpenBoardView"].text()
