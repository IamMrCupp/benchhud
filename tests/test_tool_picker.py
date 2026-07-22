"""Tests for the tool picker dialog + its Preferences wiring."""

from __future__ import annotations

from PySide6.QtCore import Qt

from benchhud.config import Config, ToolConfig
from benchhud.launcher.catalog import DetectedTool, KnownTool
from benchhud.shell.preferences import PreferencesDialog
from benchhud.shell.tool_picker import ToolPickerDialog

_FOUND = DetectedTool(
    KnownTool("OpenBoardView", "Boardview", "…", ("openboardview",)),
    command="openboardview",
    path="/usr/bin/openboardview",
)
_MISSING = DetectedTool(KnownTool("KiCad", "EDA", "…", ("kicad",)))


def _detector():
    return lambda: [_FOUND, _MISSING]


class _Scanner:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


def test_lists_found_and_missing(qtbot) -> None:
    dialog = ToolPickerDialog(_detector())
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItemCount() == 2
    assert dialog.tree.topLevelItem(0).text(1) == "installed"
    assert dialog.tree.topLevelItem(1).text(1) == "not found"


def test_found_tools_are_checked_by_default(qtbot) -> None:
    dialog = ToolPickerDialog(_detector())
    qtbot.addWidget(dialog)
    assert dialog.tree.topLevelItem(0).checkState(0) == Qt.CheckState.Checked


def test_missing_tools_are_disabled_and_not_selectable(qtbot) -> None:
    dialog = ToolPickerDialog(_detector())
    qtbot.addWidget(dialog)
    missing = dialog.tree.topLevelItem(1)
    assert not (missing.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert [t.name for t in dialog.selected_tools()] == ["OpenBoardView"]


def test_unchecking_excludes_a_tool(qtbot) -> None:
    dialog = ToolPickerDialog(_detector())
    qtbot.addWidget(dialog)
    dialog.tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert dialog.selected_tools() == []


def test_selected_tools_carry_the_command(qtbot) -> None:
    dialog = ToolPickerDialog(_detector())
    qtbot.addWidget(dialog)
    assert dialog.selected_tools() == [ToolConfig("OpenBoardView", "openboardview")]


# --- Preferences wiring ----------------------------------------------------


def test_add_tools_appends_rows(qtbot) -> None:
    prefs = PreferencesDialog(Config(), _Scanner())
    qtbot.addWidget(prefs)
    prefs.add_tools([ToolConfig("OpenBoardView", "openboardview")])
    assert prefs.tools_table.rowCount() == 1
    assert prefs.tools_table.item(0, 1).text() == "openboardview"


def test_add_tools_skips_duplicates(qtbot) -> None:
    config = Config()
    config.tools = [ToolConfig("OpenBoardView", "openboardview")]
    prefs = PreferencesDialog(config, _Scanner())
    qtbot.addWidget(prefs)
    prefs.add_tools([ToolConfig("OpenBoardView", "openboardview")])
    assert prefs.tools_table.rowCount() == 1  # not added twice


def test_detected_tools_flow_into_the_table(qtbot) -> None:
    prefs = PreferencesDialog(Config(), _Scanner())
    qtbot.addWidget(prefs)
    picker = ToolPickerDialog(_detector())
    qtbot.addWidget(picker)

    prefs.add_tools(picker.selected_tools())
    prefs.apply_to_config()

    assert prefs._config.tools == [ToolConfig("OpenBoardView", "openboardview")]


def test_preferences_has_picker_buttons(qtbot) -> None:
    prefs = PreferencesDialog(Config(), _Scanner())
    qtbot.addWidget(prefs)
    assert prefs.detect_tools_button.text() == "Detect installed…"
    assert prefs.browse_tool_button.text() == "Browse…"
