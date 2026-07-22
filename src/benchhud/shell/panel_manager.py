"""Registers panels as dock widgets on the main window.

The manager owns the mapping from ``panel_id`` to its ``QDockWidget`` and gives
each dock a stable ``objectName`` so ``QMainWindow.saveState``/``restoreState``
(driven by the config layout blob) can round-trip the arrangement.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDockWidget, QFrame, QMainWindow, QScrollArea

from benchhud.shell.panel import Panel


class PanelManager:
    """Adds and tracks dockable panels on a :class:`QMainWindow`."""

    def __init__(self, window: QMainWindow) -> None:
        self._window = window
        self._docks: dict[str, QDockWidget] = {}
        self._panels: dict[str, Panel] = {}

    def add_panel(
        self,
        panel: Panel,
        area: Qt.DockWidgetArea = Qt.DockWidgetArea.RightDockWidgetArea,
    ) -> QDockWidget:
        """Wrap ``panel`` in a dock and add it to the window. Returns the dock."""
        if panel.panel_id in self._docks:
            raise ValueError(f"duplicate panel id: {panel.panel_id!r}")

        dock = QDockWidget(panel.panel_title, self._window)
        dock.setObjectName(f"dock__{panel.panel_id}")

        # Wrap in a scroll area so a short/narrow dock scrolls instead of
        # clipping content out of reach (several docks stacked in one column
        # will otherwise squeeze the last one to nothing).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        dock.setWidget(scroll)

        self._window.addDockWidget(area, dock)
        self._docks[panel.panel_id] = dock
        self._panels[panel.panel_id] = panel
        return dock

    def panel_ids(self) -> list[str]:
        return list(self._docks)

    def dock(self, panel_id: str) -> QDockWidget | None:
        return self._docks.get(panel_id)

    def panel(self, panel_id: str) -> Panel | None:
        """The panel itself (the dock's widget is its scroll-area wrapper)."""
        return self._panels.get(panel_id)
