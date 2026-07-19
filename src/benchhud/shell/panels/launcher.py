"""Launcher panel — a button per configured external tool.

Reads the tool list from config and shows a button that opens each one via the
:class:`~benchhud.launcher.tool_launcher.ToolLauncher`. Both the tools and the
launcher are injected, so the panel is testable with a recording launcher.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from benchhud.config import ToolConfig
from benchhud.launcher.tool_launcher import ToolLauncher
from benchhud.shell.panel import Panel

_log = logging.getLogger(__name__)


class LauncherPanel(Panel):
    panel_id = "launcher"
    panel_title = "Tools"

    def __init__(
        self,
        tools: Sequence[ToolConfig] | None = None,
        launcher: ToolLauncher | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._launcher = launcher or ToolLauncher()
        self._tools = list(tools or [])
        self.buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        if not self._tools:
            layout.addWidget(QLabel("No tools configured.\nAdd [[tools]] entries to your config."))
        for tool in self._tools:
            button = QPushButton(tool.name)
            button.clicked.connect(lambda _=False, t=tool: self._launch(t))
            layout.addWidget(button)
            self.buttons[tool.name] = button
        layout.addStretch(1)

    def _launch(self, tool: ToolConfig) -> None:
        try:
            self._launcher.launch(tool)
        except Exception:
            # A bad command / missing binary must not take down the HUD.
            _log.exception("failed to launch tool %s", tool.name)
