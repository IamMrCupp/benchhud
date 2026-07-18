"""A do-nothing panel that proves the shell hosts docks.

Replaced by real panels (scope, thermal, telemetry, tools) in Phases 1-7.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from benchhud.shell.panel import Panel


class PlaceholderPanel(Panel):
    panel_id = "placeholder"
    panel_title = "Placeholder"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(
            "Placeholder panel.\n\n"
            "Real panels (scope · thermal · telemetry · tools)\n"
            "arrive in Phases 1-7."
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(label)
