"""Tests for the tool launcher + launcher panel (hardware-free)."""

from __future__ import annotations

import pytest

from benchhud.config import Config, ToolConfig
from benchhud.launcher.tool_launcher import ToolLauncher
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.launcher import LauncherPanel


class _RecordingLauncher(ToolLauncher):
    def __init__(self) -> None:
        self.launched: list[ToolConfig] = []
        super().__init__(spawn=self._record)

    def _record(self, argv: list[str]):
        return argv

    def launch(self, tool: ToolConfig):
        self.launched.append(tool)
        return super().launch(tool)


def test_launcher_parses_command_and_spawns() -> None:
    spawned: list[list[str]] = []
    launcher = ToolLauncher(spawn=lambda argv: spawned.append(argv))
    launcher.launch(ToolConfig(name="minipro", command="minipro-gui --device foo"))
    assert spawned == [["minipro-gui", "--device", "foo"]]


def test_launcher_empty_command_raises() -> None:
    launcher = ToolLauncher(spawn=lambda argv: None)
    with pytest.raises(ValueError, match="empty command"):
        launcher.launch(ToolConfig(name="broken", command="   "))


def test_panel_renders_a_button_per_tool(qtbot) -> None:
    tools = [
        ToolConfig(name="OpenBoardView", command="openboardview"),
        ToolConfig(name="minipro-gui", command="minipro-gui"),
    ]
    panel = LauncherPanel(tools, launcher=_RecordingLauncher())
    qtbot.addWidget(panel)
    assert set(panel.buttons) == {"OpenBoardView", "minipro-gui"}


def test_clicking_button_launches_tool(qtbot) -> None:
    launcher = _RecordingLauncher()
    tools = [ToolConfig(name="OpenBoardView", command="openboardview")]
    panel = LauncherPanel(tools, launcher=launcher)
    qtbot.addWidget(panel)
    panel.buttons["OpenBoardView"].click()
    assert [t.name for t in launcher.launched] == ["OpenBoardView"]


def test_panel_handles_no_tools(qtbot) -> None:
    panel = LauncherPanel([], launcher=_RecordingLauncher())
    qtbot.addWidget(panel)
    assert panel.buttons == {}


def test_bad_command_does_not_raise_from_panel(qtbot) -> None:
    tools = [ToolConfig(name="broken", command="")]
    panel = LauncherPanel(tools, launcher=ToolLauncher(spawn=lambda argv: None))
    qtbot.addWidget(panel)
    panel.buttons["broken"].click()  # must not raise (error is logged)


def test_shell_hosts_launcher_panel(qtbot) -> None:
    config = Config()
    config.tools = [ToolConfig(name="OpenBoardView", command="openboardview")]
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert "launcher" in window.panels.panel_ids()
