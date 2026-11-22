"""The application's look: one stylesheet, filled in with a light or dark palette.

The palette is burnwright's (its ``--bw-*`` properties in ``src/App.css``), light
or dark, so the HUD and burnwright look like one family (#429, #440). So is the
panel treatment: every dock a bordered card with a gutter round it, fields and
buttons with burnwright's lighter edge, Erase and Write outlined as the
destructive ones. Which palette, and any custom theme laid over it, is
``[shell] appearance``; see :mod:`benchhud.themes`.

The instrument screens keep their own colours whatever the look: the meter's
LCD cells, the power chart and the signature image match the on-stream
readout, and an oscilloscope's channel colours need a dark screen to read.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QGroupBox, QWidget

from benchhud.shell.fonts import apply_ui_font
from benchhud.shell.qt_util import heading_font, plain_font
from benchhud.themes import DARK, DEFAULT_APPEARANCE, RGBA, Look, Theme, resolve

#: The dark palette, kept under its old name: ``@name`` in the stylesheets
#: below is one of these keys. The values for every look are in
#: :mod:`benchhud.themes`.
COLOURS = DARK

_QSS = """
QWidget { background-color: @panel; color: @text; }
QMainWindow, QDialog { background-color: @window; }

QMenuBar { background-color: @chrome; border-bottom: 1px solid @border; }
QMenuBar::item { padding: 5px 10px; background: transparent; border-radius: 4px; }
QMenuBar::item:selected { background: @hover; }
QMenu { background-color: @raised; border: 1px solid @border; padding: 4px; }
QMenu::item { padding: 5px 22px; border-radius: 4px; }
QMenu::item:selected { background: @accent-soft; color: @bright; }
QMenu::separator { height: 1px; background: @border; margin: 4px 8px; }

/* A dock is a card, as a panel is in burnwright: a border all the way round,
   rounded corners, its heading inside the top edge (#440). The title bar draws
   the top of the frame and the scroll area under it the rest. Grouped docks
   show their group's name once, in small capitals (DockTitleBar, #429). */
QDockWidget { titlebar-close-icon: none; color: @muted; font-weight: 600; }
QDockWidget::title, QWidget#dockTitleBar {
    background: @panel; border: 1px solid @border; border-bottom: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}
QDockWidget::title { padding: 8px 10px 4px 10px; }
QLabel#dockTitle { color: @muted; font-weight: 600; font-size: 11px; }
QDockWidget > QScrollArea {
    background: @panel; border: 1px solid @border; border-top: none;
    border-bottom-left-radius: 8px; border-bottom-right-radius: 8px;
}
/* The drag handles between docks are the gutter: the window shows through. */
QMainWindow::separator { background: @window; width: 8px; height: 8px; }
QMainWindow::separator:hover { background: @accent-soft; }
QSplitter::handle { background: @window; }
QSplitter::handle:hover { background: @accent-soft; }

QPushButton {
    background-color: @raised; border: 1px solid @edge;
    border-radius: 6px; padding: 5px 12px; font-weight: 500;
}
QPushButton:hover { background-color: @hover; border-color: @accent; }
QPushButton:pressed { background-color: @accent-soft; }
QPushButton:focus { border-color: @accent; }
QPushButton:disabled { color: @faint; background-color: @panel; border-color: @border; }
/* The ones that destroy something (qt_util.mark_danger): Erase, Write. */
QPushButton[danger="true"] { border-color: @danger; color: @danger; }
QPushButton[danger="true"]:hover { background-color: @danger-soft; border-color: @danger; }
QPushButton[danger="true"]:disabled { color: @danger-dim; border-color: @danger-dim; }

/* Text on a group box or a coloured frame mustn't paint the window colour
   behind itself: that's what drew dark boxes behind the meter's labels. */
QLabel, QCheckBox, QRadioButton { background: transparent; }
/* A heading inside a panel (see qt_util.section_label), and secondary text. */
QLabel#sectionLabel { color: @muted; font-weight: 600; padding-top: 6px; }
QLabel#hint, QLabel#SupplyStatus, QLabel#SignatureStatus { color: @muted; }
/* A status line that's gone wrong (qt_util.set_tone). */
QLabel[tone="error"] { color: @danger; }
QLabel[tone="warning"] { color: @warning; }
QLabel[tone="ok"] { color: @ok; }
/* A notice: a tinted line with a stripe down its left edge, burnwright-style. */
QLabel#notice {
    background-color: @window; color: @text;
    border-left: 3px solid @border; border-radius: 4px; padding: 5px 8px;
}
QLabel#notice[tone="warning"] {
    background-color: @warning-soft; color: @warning; border-left-color: @warning-edge;
}
QLabel#notice[tone="error"] {
    background-color: @danger-soft; color: @danger; border-left-color: @danger;
}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: @raised; border: 1px solid @edge;
    border-radius: 6px; padding: 4px 7px;
    selection-background-color: @accent; selection-color: @accent-ink;
}
/* A spin box's arrows make it taller than a combo; less padding evens the rows. */
QSpinBox, QDoubleSpinBox { padding: 2px 7px; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: @muted;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color: @accent; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {
    color: @faint; background-color: @panel; border-color: @border;
}
QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget, QTableWidget {
    background-color: @window; border: 1px solid @border; border-radius: 6px;
}
QListWidget::item, QTreeWidget::item { padding: 3px 4px; }
QListWidget::item:selected, QTreeWidget::item:selected {
    background: @accent-soft; color: @bright;
}

/* A group is a card inside the panel: a border, its title set into the top. */
QGroupBox {
    border: 1px solid @border; border-radius: 8px;
    margin-top: 16px; padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; padding: 0 4px;
    color: @muted; font-weight: 600;
}

QToolTip {
    background-color: @raised; color: @text; border: 1px solid @border; padding: 4px;
}
QComboBox QAbstractItemView {
    background: @raised; selection-background-color: @accent-soft;
    border: 1px solid @border;
}

/* Tabs are flat labels; the one that's open carries an accent bar on the edge
   that touches its panel. */
QTabWidget::pane { border: 1px solid @border; border-radius: 6px; }
/* A group's tab row runs along the foot of its card, in the gutter. */
QTabBar { background: @window; }
QTabBar::tab {
    background: transparent; padding: 6px 10px; color: @muted;
    border: none; border-top: 2px solid transparent; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: @bright; }
QTabBar::tab:top:selected { border-bottom-color: @accent; }
QTabBar::tab:bottom:selected { border-top-color: @accent; }
QTabBar::tab:hover { color: @text; background: @panel; }

QTableWidget { background-color: @window; gridline-color: @border; }
QHeaderView::section {
    background-color: @raised; color: @muted; border: none;
    border-bottom: 1px solid @border; padding: 4px 6px;
}

QStatusBar { background: @chrome; color: @muted; border-top: 1px solid @border; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: @muted; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @border; border-radius: 5px; min-height: 24px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: @border; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:hover { background: @faint; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSlider::groove:horizontal { height: 4px; background: @edge; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent-soft; border-radius: 2px; }
QSlider::handle:horizontal {
    background: @accent; width: 12px; border-radius: 6px; margin: -5px 0;
}

/* The terminal's on-air line: red while the stream can see it. */
QLabel#TerminalOnAir { color: @danger; font-weight: bold; }
QLabel#TerminalOffAir { color: @muted; font-weight: bold; }

/* The composite sits in a darker well, the way a viewport does. */
QWidget#CompositePreview, QWidget#CompositePreview QLabel {
    background-color: @chrome; color: @muted;
}
"""


#: Check boxes and radio buttons, drawn here: the native indicator was painted
#: over by the window colour and couldn't be seen at all (#286).
_INDICATOR_QSS = """
QCheckBox, QRadioButton { spacing: 7px; }
QCheckBox::indicator, QRadioButton::indicator,
QGroupBox::indicator, QAbstractItemView::indicator {
    width: 14px; height: 14px; background-color: @raised;
    border: 1px solid @edge;
}
QCheckBox::indicator, QGroupBox::indicator, QAbstractItemView::indicator {
    border-radius: 3px;
}
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover,
QGroupBox::indicator:hover { border-color: @accent; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked,
QAbstractItemView::indicator:checked {
    background-color: @accent; border-color: @accent; image: url("{check}");
}
QRadioButton::indicator:checked {
    background-color: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
        stop: 0 @accent-ink, stop: 0.45 @accent-ink, stop: 0.5 @accent, stop: 1 @accent);
    border-color: @accent;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    border-color: @border; background-color: @panel;
}
QCheckBox::indicator:checked:disabled { background-color: @accent-soft; }
QCheckBox:disabled, QRadioButton:disabled { color: @faint; }
"""


#: A dropdown's arrow and a spin box's pair, drawn flat in the look's colours.
#: Left to Qt, the buttons got a bevelled frame with a dark edge that stood out
#: on the light look; styling the button away drops Qt's arrow with it, so the
#: arrows are drawn here instead.
_ARROW_QSS = """
QComboBox { padding-right: 22px; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 22px; border: none; background: transparent;
}
QComboBox::down-arrow { image: url("{down}"); width: 10px; height: 10px; }
QComboBox::down-arrow:disabled { image: url("{down-off}"); }
QComboBox::down-arrow:on { top: 1px; }

QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border; width: 18px; border: none; background: transparent;
}
QAbstractSpinBox::up-button { subcontrol-position: top right; margin-top: 2px; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin-bottom: 2px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background: @hover; border-radius: 4px;
}
QAbstractSpinBox::up-arrow { image: url("{up}"); width: 8px; height: 8px; }
QAbstractSpinBox::down-arrow { image: url("{down}"); width: 8px; height: 8px; }
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {
    image: url("{up-off}");
}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {
    image: url("{down-off}");
}
"""

_ARROW_PATHS = {"down": "M2 4h8L6 9z", "up": "M2 8h8L6 3z"}
_arrow_dir: Path | None = None


def _arrow(direction: str, colour: RGBA) -> str:
    """The path to an arrow SVG in ``colour``, written once per colour per run.

    A stylesheet's image has to be a file, and a custom theme's colour can be
    anything, so they're made to order in a folder that goes when the app does.
    """
    global _arrow_dir
    if _arrow_dir is None:
        _arrow_dir = Path(tempfile.mkdtemp(prefix="benchhud-arrows-"))
        atexit.register(shutil.rmtree, _arrow_dir, True)
    r, g, b, a = colour
    path = _arrow_dir / f"{direction}-{r:02x}{g:02x}{b:02x}-{round(a * 100)}.svg"
    if not path.exists():
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" '
            f'viewBox="0 0 12 12"><path d="{_ARROW_PATHS[direction]}" '
            f'fill="#{r:02x}{g:02x}{b:02x}" fill-opacity="{a:g}"/></svg>'
        )
    return path.as_posix()


def _coloured(qss: str, colours: dict[str, str]) -> str:
    """``qss`` with each ``@name`` replaced by its colour."""
    for name in sorted(colours, key=len, reverse=True):
        qss = qss.replace(f"@{name}", colours[name])
    return qss


#: The dark look's stylesheet, as it always was.
DARK_QSS = _coloured(_QSS, COLOURS)
INDICATOR_QSS = _coloured(_INDICATOR_QSS, COLOURS)

_current: Look = resolve("dark", system_dark=True, themes=[])


def style_headings(root: QWidget) -> None:
    """Set every group title under ``root`` in small caps, like a dock's heading.

    A stylesheet can't transform text, so this is done on the font. A font is
    inherited, so each group's contents are handed the plain one back.
    """
    direct = Qt.FindChildOption.FindDirectChildrenOnly
    for group in root.findChildren(QGroupBox):
        plain = plain_font(group.font())
        group.setFont(heading_font(group.font()))
        for child in group.findChildren(QWidget, options=direct):
            child.setFont(plain)


def stylesheet(look: Look | None = None) -> str:
    """The whole stylesheet for ``look`` (dark by default), check mark filled in.

    The mark is drawn on the accent, so it's dark on a light accent and white
    on a dark one.
    """
    look = look or resolve("dark", system_dark=True, themes=[])
    colours = look.qss()
    mark = "check.svg" if look.dark_check else "check-light.svg"
    check = str(files("benchhud.resources").joinpath(mark)).replace("\\", "/")
    arrows = _coloured(_ARROW_QSS, colours)
    for name, direction, tone in (
        ("down", "down", "muted"),
        ("down-off", "down", "faint"),
        ("up", "up", "muted"),
        ("up-off", "up", "faint"),
    ):
        arrows = arrows.replace("{" + name + "}", _arrow(direction, look.colours[tone]))
    indicators = _coloured(_INDICATOR_QSS, colours).replace("{check}", check)
    return _coloured(_QSS, colours) + indicators + arrows


def system_is_dark(app: QApplication) -> bool:
    """Whether the operating system is in dark mode. Unknown counts as dark."""
    return app.styleHints().colorScheme() != Qt.ColorScheme.Light


def apply_theme(
    app: QApplication,
    appearance: str = DEFAULT_APPEARANCE,
    themes: list[Theme] | None = None,
) -> Look:
    """Apply ``appearance`` and the interface font (Inter) to the application.

    The native parts Qt doesn't draw from the stylesheet (the title bar, the
    macOS menu bar, file dialogs) are told the same light or dark, except
    when following the system, where they already agree.
    """
    global _current
    apply_ui_font(app)
    hints = app.styleHints()
    if appearance == "system":
        hints.unsetColorScheme()
    look = resolve(appearance, system_dark=system_is_dark(app), themes=themes or [])
    tell_native(hints, appearance, look)
    _current = look
    app.setStyleSheet(stylesheet(look))
    return look


def tell_native(hints, appearance: str, look: Look) -> None:
    """Ask for the native light or dark to match ``look``, unless following the system.

    Only some platforms honour it (macOS and Windows do; the test platform
    doesn't), which is fine: it's the parts the stylesheet doesn't reach.
    """
    if appearance == "system":
        return
    hints.setColorScheme(Qt.ColorScheme.Dark if look.base == "dark" else Qt.ColorScheme.Light)


def current_look() -> Look:
    """The look last applied (dark before anything has been)."""
    return _current


def colour(name: str) -> QColor:
    """One of the current look's colours, for a widget that paints its own text.

    A table item's colour isn't reached by the stylesheet, so a panel asks for
    it when it fills a row.
    """
    r, g, b, a = _current.colours[name]
    return QColor(r, g, b, round(a * 255))
