"""Base class for HUD panels.

A panel is just a ``QWidget`` with two bits of metadata: a stable ``panel_id``
(used for the dock's ``objectName``, which is what makes Qt's layout
save/restore work) and a human ``panel_title`` shown in the dock title bar.
Concrete panels — scope, thermal, telemetry, tools — subclass this in later
phases.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class Panel(QWidget):
    """A dockable HUD panel.

    Subclasses must set a non-empty, stable :attr:`panel_id`.
    """

    panel_id: str = ""
    panel_title: str = "Panel"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not self.panel_id:
            raise ValueError(f"{type(self).__name__} must set a non-empty panel_id")
        self.setObjectName(f"panel__{self.panel_id}")
