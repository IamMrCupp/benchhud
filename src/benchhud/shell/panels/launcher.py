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
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from benchhud.config import ToolConfig
from benchhud.launcher.catalog import is_command_line
from benchhud.launcher.tool_launcher import ToolLauncher
from benchhud.shell.panel import PANEL_MARGINS, Panel

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
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*PANEL_MARGINS)
        self.set_tools(tools or [])

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
            if tool.terminal:
                button.setToolTip("Command line: opens in the Terminal panel, ready for arguments")
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
            return
        try:
            self._launcher.launch(tool)
        except Exception:
            # A bad command / missing binary must not take down the HUD.
            _log.exception("failed to launch tool %s", tool.name)
