"""Application dark theme.

A single QSS stylesheet applied at startup so the HUD reads as one dark
bench/stream app rather than default-Qt light.

The colours and the panel treatment are burnwright's (#440): its ``--bw-*``
dark palette, panels as bordered cards with a gutter between them, headings in
small caps. The two apps sit on the same bench and should look it. The meter's
LCD stays light on purpose — it's an instrument face glowing against dark
equipment, not part of the chrome.
"""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDockWidget, QGroupBox, QWidget

from benchhud.shell.qt_util import heading_font, plain_font

#: The palette, in one place. ``@name`` in the stylesheets below is replaced
#: with these, longest name first.
COLOURS = {
    "window": "#151517",  # --bw-bg: behind everything, the gutters between panels
    "chrome": "#101012",  # the composite's well: a shade under the window
    "panel": "#1f1f22",  # --bw-panel: a dock's body
    "raised": "#2a2a2e",  # --bw-control-bg: fields and buttons
    "hover": "#34343a",  # --bw-control-hover
    "border": "#3a3a40",  # --bw-border: around panels
    "edge": "#55555d",  # --bw-control-border: around fields and buttons
    "text": "#ececef",  # --bw-fg
    "bright": "#ffffff",
    "muted": "#a3a3ab",  # --bw-muted
    "faint": "#6e6e76",
    "accent": "#8fb0ff",  # --bw-accent
    "accent-soft": "#24345c",
    "accent-ink": "#101422",
    "danger": "#ff9a92",  # --bw-danger
    "danger-soft": "#4a1f1c",
    "danger-dim": "#7d524f",  # danger at half strength, for a disabled button
    "warning": "#ffc66b",  # --bw-warning
    "ok": "#7fd6a0",  # --bw-ok
}

#: For text a panel colours itself: an error line, a warning.
DANGER = COLOURS["danger"]
WARNING = COLOURS["warning"]

DARK_QSS = """
QWidget { background-color: @panel; color: @text; }
QMainWindow, QDialog { background-color: @window; }

QMenuBar { background-color: @window; border-bottom: 1px solid @border; }
QMenuBar::item { padding: 5px 10px; background: transparent; border-radius: 4px; }
QMenuBar::item:selected { background: @hover; }
QMenu { background-color: @raised; border: 1px solid @border; padding: 4px; }
QMenu::item { padding: 5px 22px; border-radius: 4px; }
QMenu::item:selected { background: @accent-soft; color: @bright; }
QMenu::separator { height: 1px; background: @border; margin: 4px 8px; }

/* A dock is a card, as a panel is in burnwright: a border all the way round,
   rounded corners, its heading inside the top edge. The title draws the top of
   the frame and the scroll area under it the rest. */
QDockWidget { titlebar-close-icon: none; color: @muted; font-weight: 600; }
QDockWidget::title {
    background: @panel; padding: 8px 10px 4px 10px;
    border: 1px solid @border; border-bottom: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
}
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
/* The ones that destroy something (see qt_util.mark_danger): Erase, Write. */
QPushButton[danger="true"] { border-color: @danger; color: @danger; }
QPushButton[danger="true"]:hover { background-color: @danger-soft; border-color: @danger; }
QPushButton[danger="true"]:disabled { color: @danger-dim; border-color: @danger-dim; }

/* Text on a group box or a coloured frame mustn't paint the window colour
   behind itself: that's what drew dark boxes behind the meter's labels. */
QLabel, QCheckBox, QRadioButton { background: transparent; }
/* A heading inside a panel (see qt_util.section_label), and secondary text. */
QLabel#sectionLabel { color: @muted; font-weight: 600; padding-top: 6px; }
QLabel#hint, QLabel#SupplyStatus, QLabel#SignatureStatus { color: @muted; }

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

QStatusBar { background: @window; color: @muted; border-top: 1px solid @border; }
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

/* The composite sits in a darker well, the way a viewport does. */
QWidget#CompositePreview, QWidget#CompositePreview QLabel {
    background-color: @chrome; color: @muted;
}
"""


#: Check boxes and radio buttons, drawn here: the native indicator was painted
#: over by the window colour and couldn't be seen at all (#286).
INDICATOR_QSS = """
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


def _coloured(qss: str) -> str:
    """``qss`` with each ``@name`` replaced by its colour."""
    for name in sorted(COLOURS, key=len, reverse=True):
        qss = qss.replace(f"@{name}", COLOURS[name])
    return qss


DARK_QSS = _coloured(DARK_QSS)
INDICATOR_QSS = _coloured(INDICATOR_QSS)


def style_headings(root: QWidget) -> None:
    """Set every dock's and group's title under ``root`` in small caps.

    A stylesheet can't transform text, so this is done on the font. A font is
    inherited, so each title's contents are handed the plain one back.
    """
    for dock in root.findChildren(QDockWidget):
        _caps_title(dock, [dock.widget()] if dock.widget() is not None else [])
    for group in root.findChildren(QGroupBox):
        direct = Qt.FindChildOption.FindDirectChildrenOnly
        _caps_title(group, group.findChildren(QWidget, options=direct))


def _caps_title(widget: QWidget, contents: list[QWidget]) -> None:
    plain = plain_font(widget.font())
    widget.setFont(heading_font(widget.font()))
    for child in contents:
        child.setFont(plain)


def stylesheet() -> str:
    """The whole stylesheet, with the check mark's path filled in."""
    check = str(files("benchhud.resources").joinpath("check.svg")).replace("\\", "/")
    return DARK_QSS + INDICATOR_QSS.replace("{check}", check)


def apply_theme(app: QApplication) -> None:
    """Apply the dark theme to the application."""
    app.setStyleSheet(stylesheet())
