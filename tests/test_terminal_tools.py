"""Command-line tools from the Tools pane (#225): the catalog, the config, and where they open."""

from __future__ import annotations

import sys
from typing import ClassVar

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from benchhud.config import Config, ToolConfig
from benchhud.jobs.store import JobStore
from benchhud.launcher.catalog import KNOWN_TOOLS, DetectedTool
from benchhud.launcher.tool_launcher import NoTerminalError, ToolLauncher, system_terminal_argv
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.launcher import LauncherPanel
from benchhud.shell.preferences import PreferencesDialog
from benchhud.shell.tool_picker import ToolPickerDialog

FLASHROM = ToolConfig(name="flashrom", command="flashrom", terminal=True)
VIEWER = ToolConfig(name="OpenBoardView", command="openboardview")

# --- the catalog and the config --------------------------------------------------------


def test_cli_tools_are_marked_and_their_guis_are_real_entries() -> None:
    by_name = {t.name: t for t in KNOWN_TOOLS}
    for name in ("minipro", "flashrom", "avrdude", "sigrok-cli", "OpenOCD", "picocom", "v4l2-ctl"):
        assert by_name[name].terminal, name
    for name in ("OpenBoardView", "PulseView", "minipro GUI", "qv4l2", "GtkTerm"):
        assert not by_name[name].terminal, name
    assert by_name["minipro"].gui[:2] == ("minipro GUI", "qtl866")
    for tool in KNOWN_TOOLS:
        assert all(g in by_name for g in tool.gui), tool.name


def test_qv4l2_and_v4l2_ctl_are_separate_entries() -> None:
    """Whether a tool needs a terminal belongs to the program, not to the entry."""
    by_name = {t.name: t for t in KNOWN_TOOLS}
    assert by_name["qv4l2"].commands == ("qv4l2",)
    assert by_name["v4l2-ctl"].commands == ("v4l2-ctl",)


def test_the_terminal_flag_round_trips_through_config() -> None:
    config = Config.from_dict(
        {
            "tools": [
                {"name": "flashrom", "command": "flashrom", "terminal": True},
                {"name": "OpenBoardView", "command": "openboardview"},
            ]
        }
    )
    assert [t.terminal for t in config.tools] == [True, False]
    assert config.to_dict()["tools"][0]["terminal"] is True


# --- a system terminal window, the fallback -------------------------------------------


def test_macos_opens_terminal_app_with_the_command() -> None:
    argv = system_terminal_argv('echo "hi"', "darwin")
    assert argv[0] == "osascript"
    assert 'do script "echo \\"hi\\""' in argv[2]


def test_windows_opens_a_console_that_stays() -> None:
    assert system_terminal_argv("flashrom", "win32") == ["cmd.exe", "/k", "flashrom"]


def test_linux_uses_the_first_terminal_it_finds_and_keeps_a_shell() -> None:
    only_gnome = {"gnome-terminal": "/usr/bin/gnome-terminal"}.get
    argv = system_terminal_argv("flashrom", "linux", only_gnome)
    assert argv[:3] == ["gnome-terminal", "--", "sh"]
    assert argv[-1].startswith("flashrom; exec ")


def test_linux_with_no_terminal_says_so() -> None:
    with pytest.raises(NoTerminalError):
        system_terminal_argv("flashrom", "linux", lambda _name: None)


def test_launcher_spawns_the_system_terminal() -> None:
    spawned: list[list[str]] = []
    ToolLauncher(spawn=spawned.append).launch_in_system_terminal(FLASHROM, platform="win32")
    assert spawned == [["cmd.exe", "/k", "flashrom"]]


# --- the Tools pane, the picker and Preferences ---------------------------------------------


def test_a_terminal_tool_is_handed_over_not_spawned(qtbot) -> None:
    spawned: list[list[str]] = []
    panel = LauncherPanel([FLASHROM, VIEWER], ToolLauncher(spawn=spawned.append))
    qtbot.addWidget(panel)
    asked: list[ToolConfig] = []
    panel.terminal_tool_requested.connect(asked.append)
    panel.buttons["flashrom"].click()
    panel.buttons["OpenBoardView"].click()
    assert asked == [FLASHROM]
    assert spawned == [["openboardview"]]  # GUI tools launch as before
    assert ">_" in panel.buttons["flashrom"].text()


def test_the_picker_carries_the_terminal_flag(qtbot) -> None:
    by_name = {t.name: t for t in KNOWN_TOOLS}
    detected = [
        DetectedTool(by_name["flashrom"], command="flashrom", path="/usr/bin/flashrom"),
        DetectedTool(by_name["minipro GUI"], command="minipro-gui", path="/usr/bin/minipro-gui"),
    ]
    dialog = ToolPickerDialog(detector=lambda: detected)
    qtbot.addWidget(dialog)
    chosen = {t.name: t.terminal for t in dialog.selected_tools()}
    assert chosen == {"flashrom": True, "minipro GUI": False}
    assert "GUI alternative" not in dialog.tree.topLevelItem(0).toolTip(0)  # flashrom has none
    assert "Command line" in dialog.tree.topLevelItem(0).toolTip(0)


class _NoDevices:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


def test_preferences_keep_and_edit_the_terminal_flag(qtbot) -> None:
    config = Config()
    config.tools = [FLASHROM, VIEWER]
    dialog = PreferencesDialog(config, _NoDevices())
    qtbot.addWidget(dialog)
    dialog.tools_table.item(1, 2).setCheckState(Qt.CheckState.Checked)  # mark OpenBoardView
    dialog.apply_to_config()
    assert [t.terminal for t in config.tools] == [True, True]


# --- where the window opens it ------------------------------------------------------------


class FakeSession:
    instances: ClassVar[list[FakeSession]] = []

    def __init__(self, argv, *, on_output, on_exit, **_kw) -> None:
        self.argv, self.written, self.running = list(argv), [], False
        FakeSession.instances.append(self)

    def start(self):
        self.running = True

    def write(self, text):
        self.written.append(text)

    def resize(self, *_a):
        pass

    def close(self):
        self.running = False


class FakeView(QWidget):
    def setUrl(self, url) -> None:
        pass


def _window(qtbot, *, terminal: bool, target: str = "shell"):
    FakeSession.instances.clear()
    config = Config()
    config.browser_sources.port = 0
    config.terminal.enabled = terminal
    config.terminal.target = target
    config.tools = [FLASHROM]
    spawned: list[list[str]] = []
    window = MainWindow(
        config,
        job_store=JobStore(":memory:"),
        terminal_view_factory=FakeView,
        terminal_session_factory=FakeSession,
    )
    window.launcher_panel._launcher = ToolLauncher(spawn=spawned.append)
    qtbot.addWidget(window)
    window.start_browser_sources()
    return window, spawned


@pytest.mark.skipif(sys.platform == "win32", reason="the terminal panel is POSIX-only")
def test_with_the_terminal_on_the_command_waits_at_the_prompt(qtbot) -> None:
    window, spawned = _window(qtbot, terminal=True)
    try:
        window.launcher_panel.buttons["flashrom"].click()
        assert FakeSession.instances[-1].written == ["flashrom "]  # no Enter: add arguments
        assert spawned == []
    finally:
        window.close()


def test_with_the_terminal_off_it_opens_a_system_terminal(qtbot) -> None:
    window, spawned = _window(qtbot, terminal=False)
    try:
        window.launcher_panel.buttons["flashrom"].click()
        assert len(spawned) == 1 and "flashrom" in " ".join(spawned[0])
    finally:
        window.close()


@pytest.mark.skipif(sys.platform == "win32", reason="the terminal panel is POSIX-only")
def test_a_serial_console_is_only_dropped_with_a_yes(qtbot) -> None:
    window, spawned = _window(qtbot, terminal=True, target="/dev/ttyUSB9")
    try:
        window.confirm_terminal_switch = lambda _target: False
        window.launcher_panel.buttons["flashrom"].click()
        assert window._config.terminal.target == "/dev/ttyUSB9"  # left alone
        window.confirm_terminal_switch = lambda _target: True
        window.launcher_panel.buttons["flashrom"].click()
        assert window._config.terminal.target == "shell"
        assert FakeSession.instances[-1].written == ["flashrom "]
        assert spawned == []
    finally:
        window.close()
