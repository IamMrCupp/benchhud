"""Registers panels as dock widgets on the main window.

The manager owns the mapping from ``panel_id`` to its ``QDockWidget`` and gives
each dock a stable ``objectName`` so ``QMainWindow.saveState``/``restoreState``
(driven by the config layout blob) can round-trip the arrangement.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QScrollArea,
    QWidget,
)

from benchhud.shell.panel import Panel
from benchhud.shell.wheel_guard import install as install_wheel_guard


class DockTitleBar(QWidget):
    """A dock's title bar showing its group's heading instead of its own title.

    Tabbed docks already name themselves in their tabs, so the bar above them
    carries the group ("BENCH", "TOOLS") once, burnwright-style (#429). Mouse
    events pass through to the dock, which keeps drag-to-move and
    double-click-to-float working; showing and hiding is the View menu's job.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dockTitleBar")
        # A plain QWidget only paints a stylesheet background/border with this.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.label = QLabel(text)
        self.label.setObjectName("dockTitle")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        font = QFont(self.label.font())
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        self.label.setFont(font)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def text(self) -> str:
        return self.label.text()


class PanelManager:
    """Adds and tracks dockable panels on a :class:`QMainWindow`."""

    def __init__(self, window: QMainWindow) -> None:
        self._window = window
        self._docks: dict[str, QDockWidget] = {}
        self._panels: dict[str, Panel] = {}

    def add_panel(
        self,
        panel: Panel,
        area: Qt.DockWidgetArea | None = Qt.DockWidgetArea.RightDockWidgetArea,
    ) -> QDockWidget:
        """Wrap ``panel`` in a dock and add it to the window. Returns the dock.

        With ``area`` None the dock is made but not placed yet: the caller places
        it (the main window's groups, #429). Placing a dock and then moving it into
        a tab group leaves Qt's tab bar for the old arrangement behind.
        """
        if panel.panel_id in self._docks:
            raise ValueError(f"duplicate panel id: {panel.panel_id!r}")

        # Qt reads a lone "&" in a dock's title as a shortcut marker and drops
        # it: "Pinout & protocols" showed as "Pinout _protocols" (#430).
        dock = QDockWidget(panel.panel_title.replace("&", "&&"), self._window)
        dock.setObjectName(f"dock__{panel.panel_id}")

        # Wrap in a scroll area so a short/narrow dock scrolls instead of
        # clipping content out of reach (several docks stacked in one column
        # will otherwise squeeze the last one to nothing).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        dock.setWidget(scroll)
        # The wheel scrolls the dock rather than the controls it passes over (#320).
        install_wheel_guard(scroll)

        if area is not None:
            self._window.addDockWidget(area, dock)
        self._docks[panel.panel_id] = dock
        self._panels[panel.panel_id] = panel
        return dock

    def set_heading(self, panel_id: str, text: str) -> None:
        """Replace the dock's native title bar with a group heading."""
        dock = self._docks[panel_id]
        dock.setTitleBarWidget(DockTitleBar(text, dock))

    def panel_ids(self) -> list[str]:
        return list(self._docks)

    def dock(self, panel_id: str) -> QDockWidget | None:
        return self._docks.get(panel_id)

    def panel(self, panel_id: str) -> Panel | None:
        """The panel itself (the dock's widget is its scroll-area wrapper)."""
        return self._panels.get(panel_id)
