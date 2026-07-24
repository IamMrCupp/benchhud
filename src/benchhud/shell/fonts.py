"""Register bundled fonts with Qt.

The DSEG 7-segment font (OFL-1.1, under ``resources/fonts``) gives the power/DMM
meter real segmented-LCD digits instead of a monospace approximation.

Registration needs a running ``QApplication`` and is done once, lazily, then
cached — panels call :func:`seven_segment_family` freely. It returns the family
name, or ``None`` when the font can't be loaded (a stripped build, an old Qt), so
callers fall back to monospace rather than break.
"""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtGui import QFontDatabase

_SEVEN_SEGMENT_FILE = "DSEG7Classic-Bold.ttf"

_resolved = False
_family: str | None = None


def seven_segment_family() -> str | None:
    """Family name of the bundled 7-segment font, or ``None`` if unavailable."""
    global _resolved, _family
    if _resolved:
        return _family
    _resolved = True
    try:
        path = files("benchhud.resources").joinpath("fonts", _SEVEN_SEGMENT_FILE)
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id != -1:
            families = QFontDatabase.applicationFontFamilies(font_id)
            _family = families[0] if families else None
    except Exception:  # pragma: no cover - defensive; a broken font shouldn't crash the HUD
        _family = None
    return _family
