"""The benchhud main window.

Currently a minimal :class:`~PySide6.QtWidgets.QMainWindow` with a placeholder
central widget. The dockable panel manager lands in §C.3 and config-driven
geometry restore in §C.2; this file grows into the shell host.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QWidget

from benchhud import __version__


class MainWindow(QMainWindow):
    """Top-level HUD window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BenchhudMainWindow")
        self.setWindowTitle("benchhud")

        placeholder = QLabel(
            "benchhud — HUD shell\n\n"
            "Panels (scope · thermal · telemetry · tools) land in Phase 0 §C.3."
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setObjectName("PlaceholderCentral")
        self.setCentralWidget(placeholder)

        self.statusBar().showMessage(f"benchhud {__version__}")
        self.resize(1280, 800)
