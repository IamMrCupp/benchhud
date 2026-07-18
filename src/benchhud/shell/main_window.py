"""The benchhud main window.

A minimal :class:`~PySide6.QtWidgets.QMainWindow` that restores its geometry and
dock layout from :class:`~benchhud.config.Config` on construction and persists
them back on close. The dockable panel manager lands in §C.3; the persistence
mechanism here already handles dock/toolbar state via ``saveState``/
``restoreState``.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QMainWindow, QWidget

from benchhud import __version__
from benchhud.config import Config, PersistCallback


class MainWindow(QMainWindow):
    """Top-level HUD window."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        on_persist: PersistCallback | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or Config()
        self._on_persist = on_persist

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

        self._apply_config()

    def _apply_config(self) -> None:
        shell = self._config.shell
        self.resize(shell.window_width, shell.window_height)
        if shell.layout:
            self.restoreState(QByteArray.fromBase64(shell.layout.encode("ascii")))

    def persist_into_config(self) -> None:
        """Write current window geometry + dock layout back into the config."""
        shell = self._config.shell
        shell.window_width = self.width()
        shell.window_height = self.height()
        shell.layout = bytes(self.saveState().toBase64()).decode("ascii")

    def closeEvent(self, event: QCloseEvent) -> None:
        self.persist_into_config()
        if self._on_persist is not None:
            self._on_persist(self._config)
        super().closeEvent(event)
