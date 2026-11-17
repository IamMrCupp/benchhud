"""Application dark theme.

A single QSS stylesheet applied at startup so the HUD reads as one dark
bench/stream app rather than default-Qt light. Teal accent deliberately echoes
the meter's cyan LCD, which stays light on purpose — it looks like an instrument
screen glowing against dark equipment.
"""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtWidgets import QApplication

#: The palette, in one place. ``@name`` in the stylesheets below is replaced
#: with these, longest name first.
COLOURS = {
    "window": "#1c1f25",  # behind everything: the gaps between panels
    "chrome": "#16181d",  # menu bar, status bar, the composite's surround
    "panel": "#23262d",  # a dock's body
    "raised": "#2b2f38",  # cards, fields, buttons
    "hover": "#353a45",
    "border": "#343945",
    "text": "#d8dee9",
    "bright": "#f0f4f8",
    "muted": "#8f9aab",
    "faint": "#636b7a",
    "accent": "#4a9db5",
    "accent-soft": "#2c4f5b",
    "accent-ink": "#0e1418",
}

DARK_QSS = """
QWidget { background-color: @panel; color: @text; }
QMainWindow, QDialog { background-color: @window; }

QMenuBar { background-color: @chrome; border-bottom: 1px solid @border; }
QMenuBar::item { padding: 5px 10px; background: transparent; border-radius: 4px; }
QMenuBar::item:selected { background: @hover; }
QMenu { background-color: @raised; border: 1px solid @border; padding: 4px; }
QMenu::item { padding: 5px 22px; border-radius: 4px; }
QMenu::item:selected { background: @accent-soft; color: @bright; }
QMenu::separator { height: 1px; background: @border; margin: 4px 8px; }

/* A dock's title is a quiet heading, not a boxed bar: the panel under it is
   what should draw the eye. */
QDockWidget { titlebar-close-icon: none; color: @muted; font-weight: 600; }
QDockWidget::title {
    background: @panel; padding: 7px 10px; border-bottom: 1px solid @border;
}
/* The drag handles between docks: the window colour, so each column reads as
   its own surface. */
QMainWindow::separator { background: @window; width: 5px; height: 5px; }
QMainWindow::separator:hover { background: @accent-soft; }
QSplitter::handle { background: @window; }
QSplitter::handle:hover { background: @accent-soft; }

QPushButton {
    background-color: @raised; border: 1px solid @border;
    border-radius: 5px; padding: 5px 12px;
}
QPushButton:hover { background-color: @hover; border-color: @faint; }
QPushButton:pressed { background-color: @accent; color: @accent-ink; }

QPushButton:disabled { color: @faint; background-color: @panel; }
QPushButton:focus { border-color: @accent; }

/* Text on a group box or a coloured frame mustn't paint the window colour
   behind itself: that's what drew dark boxes behind the meter's labels. */
QLabel, QCheckBox, QRadioButton { background: transparent; }
/* A heading inside a panel (see qt_util.section_label), and secondary text. */
QLabel#sectionLabel { color: @muted; font-weight: 600; padding-top: 6px; }
QLabel#hint, QLabel#SupplyStatus, QLabel#SignatureStatus { color: @muted; }

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: @raised; border: 1px solid @border;
    border-radius: 5px; padding: 4px 7px;
    selection-background-color: @accent; selection-color: @accent-ink;
}
/* A spin box's arrows make it taller than a combo; less padding evens the rows. */
QSpinBox, QDoubleSpinBox { padding: 2px 7px; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: @faint;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color: @accent; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {
    color: @faint; background-color: @panel;
}
QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget, QTableWidget {
    background-color: @window; border: 1px solid @border; border-radius: 6px;
}
QListWidget::item, QTreeWidget::item { padding: 3px 4px; }
QListWidget::item:selected, QTreeWidget::item:selected {
    background: @accent-soft; color: @bright;
}

/* A group is a card: one raised surface with its title set into the top edge. */
QGroupBox {
    background-color: @raised; border: 1px solid @border; border-radius: 8px;
    margin-top: 16px; padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; padding: 0 4px;
    color: @muted; font-weight: 600;
}
QGroupBox QComboBox, QGroupBox QSpinBox, QGroupBox QDoubleSpinBox,
QGroupBox QLineEdit { background-color: @panel; }
QGroupBox QPushButton { background-color: @hover; }
QGroupBox QPushButton:hover { border-color: @muted; }
QGroupBox QPushButton:pressed { background-color: @accent; }
QGroupBox QPushButton:disabled { background-color: @raised; }

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
QTabBar { background: @panel; }
QTabBar::tab {
    background: transparent; padding: 6px 10px; color: @muted;
    border: none; border-top: 2px solid transparent; border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: @bright; }
QTabBar::tab:top:selected { border-bottom-color: @accent; }
QTabBar::tab:bottom:selected { border-top-color: @accent; }
QTabBar::tab:hover { color: @text; background: @raised; }

QTableWidget { background-color: @window; gridline-color: @border; }
QHeaderView::section {
    background-color: @raised; color: @muted; border: none;
    border-bottom: 1px solid @border; padding: 4px 6px;
}

QStatusBar { background: @chrome; color: @muted; border-top: 1px solid @border; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: @muted; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @hover; border-radius: 5px; min-height: 24px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: @hover; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:hover { background: @faint; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSlider::groove:horizontal { height: 4px; background: @hover; border-radius: 2px; }
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
    width: 14px; height: 14px; background-color: @chrome;
    border: 1px solid @muted;
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


def stylesheet() -> str:
    """The whole stylesheet, with the check mark's path filled in."""
    check = str(files("benchhud.resources").joinpath("check.svg")).replace("\\", "/")
    return DARK_QSS + INDICATOR_QSS.replace("{check}", check)


def apply_theme(app: QApplication) -> None:
    """Apply the dark theme to the application."""
    app.setStyleSheet(stylesheet())
