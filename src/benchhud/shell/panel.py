"""Base class for HUD panels.

A panel is just a ``QWidget`` with two bits of metadata: a stable ``panel_id``
(used for the dock's ``objectName``, which is what makes Qt's layout
save/restore work) and a human ``panel_title`` shown in the dock title bar.
Concrete panels — scope, thermal, telemetry, tools — subclass this in later
phases.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

#: Margins inside a dock panel. macOS's default of 20 px a side spent 40 px of
#: a narrow dock on nothing (#230).
PANEL_MARGINS = (8, 8, 8, 8)


class Panel(QWidget):
    """A dockable HUD panel.

    Subclasses must set a non-empty, stable :attr:`panel_id`.
    """

    panel_id: str = ""
    panel_title: str = "Panel"

    #: Whether this panel's content is sensitive — must never reach a stream
    #: surface. The compositor and the browser-source whitelist exclude a
    #: sensitive panel (see :mod:`benchhud.output.onair`). It defaults safe for
    #: the bench panels; recon panels override it to ``True``, so a surface is
    #: off air unless someone has decided it is safe to show.
    sensitive: bool = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not self.panel_id:
            raise ValueError(f"{type(self).__name__} must set a non-empty panel_id")
        self.setObjectName(f"panel__{self.panel_id}")
