"""Launcher panel — a button per configured external tool.

Reads the tool list from config and shows a button that opens each one via the
:class:`~benchhud.launcher.tool_launcher.ToolLauncher`. Both the tools and the
launcher are injected, so the panel is testable with a recording launcher.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from benchhud.config import ToolConfig
from benchhud.launcher.catalog import is_command_line
from benchhud.launcher.tool_launcher import ToolLauncher
from benchhud.shell.panel import PANEL_MARGINS, Panel
from benchhud.shell.qt_util import fit_combo_popup

_log = logging.getLogger(__name__)

#: Shown with no tools. It points at the app, not the config file, because
#: most people using this will never open a TOML file.
EMPTY_HINT = "No tools yet.\nAdd some in Preferences → Tools."


class LauncherPanel(Panel):
    panel_id = "launcher"
    panel_title = "Tools"

    #: A command-line tool was clicked (#225). The main window opens it in a
    #: terminal: the Terminal panel when it can, else a system terminal window.
    terminal_tool_requested = Signal(object)
    #: A tool was started: the tool, and its process (None for a command-line
    #: tool, which is typed into a terminal and has no process of its own).
    tool_launched = Signal(object, object)
    #: A scene was picked in the "On stream" row (#455).
    scene_requested = Signal(str)

    def __init__(
        self,
        tools: Sequence[ToolConfig] | None = None,
        launcher: ToolLauncher | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._launcher = launcher or ToolLauncher()
        self._tools: list[ToolConfig] = []
        self.buttons: dict[str, QPushButton] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*PANEL_MARGINS)
        # Which OBS scene is live, and a way to change it (#455). Hidden until
        # OBS is connected: with nothing to pick from it's only clutter.
        self.scene_combo = QComboBox()
        self.scene_combo.setToolTip("The scene OBS is showing")
        self.scene_combo.activated.connect(
            lambda _i: self.scene_requested.emit(self.scene_combo.currentText())
        )
        self._scene_row = QWidget()
        scene_row = QHBoxLayout(self._scene_row)
        scene_row.setContentsMargins(0, 0, 0, 0)
        scene_row.addWidget(QLabel("On stream"))
        scene_row.addWidget(self.scene_combo, stretch=1)
        self._scene_row.setVisible(False)
        outer.addWidget(self._scene_row)
        self._layout = QVBoxLayout()
        outer.addLayout(self._layout, stretch=1)
        self.set_tools(tools or [])

    def set_scenes(self, scenes: Sequence[str], current: str | None = None) -> None:
        """Offer ``scenes`` in the "On stream" row; none hides the row."""
        self.scene_combo.blockSignals(True)
        self.scene_combo.clear()
        self.scene_combo.addItems(list(scenes))
        self.scene_combo.blockSignals(False)
        fit_combo_popup(self.scene_combo)
        self._scene_row.setVisible(bool(scenes))
        self.set_current_scene(current)

    def set_current_scene(self, scene: str | None) -> None:
        """Show ``scene`` as the live one, without asking for it."""
        index = self.scene_combo.findText(scene) if scene else -1
        if index >= 0:
            self.scene_combo.blockSignals(True)
            self.scene_combo.setCurrentIndex(index)
            self.scene_combo.blockSignals(False)

    def set_tools(self, tools: Sequence[ToolConfig]) -> None:
        """Rebuild the buttons from ``tools`` (called again when Preferences change them)."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        # A known command-line tool runs in a terminal even when its config
        # entry predates the flag (#230).
        self._tools = [
            replace(t, terminal=True) if not t.terminal and is_command_line(t.command) else t
            for t in tools
        ]
        self.buttons = {}
        if not self._tools:
            self._layout.addWidget(QLabel(EMPTY_HINT))
        for tool in self._tools:
            button = QPushButton(f"{tool.name}  >_" if tool.terminal else tool.name)
            tips = []
            if tool.terminal:
                tips.append("Command line: opens in the Terminal panel, ready for arguments")
            if tool.scene:
                tips.append(f"OBS scene: {tool.scene}")
            button.setToolTip("\n".join(tips))
            button.clicked.connect(lambda _=False, t=tool: self._launch(t))
            self._layout.addWidget(button)
            self.buttons[tool.name] = button
        self._layout.addStretch(1)

    @property
    def launcher(self) -> ToolLauncher:
        return self._launcher

    def _launch(self, tool: ToolConfig) -> None:
        if tool.terminal:
            self.terminal_tool_requested.emit(tool)
            self.tool_launched.emit(tool, None)
            return
        try:
            process = self._launcher.launch(tool)
        except Exception:
            # A bad command / missing binary must not take down the HUD.
            _log.exception("failed to launch tool %s", tool.name)
            return
        self.tool_launched.emit(tool, process)
