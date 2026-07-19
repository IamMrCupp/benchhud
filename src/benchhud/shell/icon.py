"""Load the bundled application icon.

The icon lives in the ``benchhud.resources`` package so it ships in the wheel.
Loading requires a running ``QGuiApplication`` (Qt pixmap constraint), so call
this after the ``QApplication`` exists.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from PySide6.QtGui import QIcon, QPixmap


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Return the benchhud application icon."""
    path = files("benchhud.resources").joinpath("icon.png")
    return QIcon(QPixmap(str(path)))
