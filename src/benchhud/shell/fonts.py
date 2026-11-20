"""Register bundled fonts with Qt.

The DSEG 7-segment font (OFL-1.1, under ``resources/fonts``) gives the power/DMM
meter real segmented-LCD digits instead of a monospace approximation. Inter
(OFL-1.1, same folder) is the interface font, matching burnwright (#429).

Text drawn onto the stream (the telemetry readout, the graph legend, the
power-on signature) keeps the platform's own font via :func:`stream_font`, so
restyling the interface never changes what viewers see.

Registration needs a running ``QApplication`` and is done once, lazily, then
cached — panels call :func:`seven_segment_family` freely. It returns the family
name, or ``None`` when the font can't be loaded (a stripped build, an old Qt), so
callers fall back to monospace rather than break.
"""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

_SEVEN_SEGMENT_FILE = "DSEG7Classic-Bold.ttf"
#: Regular for text, SemiBold for headings; a bold request falls to SemiBold.
_UI_FILES = ("Inter-Regular.ttf", "Inter-SemiBold.ttf")

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


_ui_resolved = False
_ui_family: str | None = None


def ui_font_family() -> str | None:
    """Family name of the bundled interface font (Inter), or ``None`` if unavailable."""
    global _ui_resolved, _ui_family
    if _ui_resolved:
        return _ui_family
    _ui_resolved = True
    try:
        for name in _UI_FILES:
            path = files("benchhud.resources").joinpath("fonts", name)
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id != -1 and _ui_family is None:
                families = QFontDatabase.applicationFontFamilies(font_id)
                _ui_family = families[0] if families else None
    except Exception:  # pragma: no cover - defensive; the system font is a fine fallback
        _ui_family = None
    return _ui_family


def apply_ui_font(app: QApplication) -> None:
    """Make Inter the application font, at the platform's own size."""
    family = ui_font_family()
    if family is None:
        return
    font = QFont(app.font())
    font.setFamily(family)
    app.setFont(font)


def stream_font() -> QFont:
    """The font for text drawn onto the stream: the platform's, never the app's.

    ``QFont()`` follows the application font, so once Inter is applied it would
    quietly restyle the on-stream labels too. The system font is what they used
    before the interface font existed.
    """
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
