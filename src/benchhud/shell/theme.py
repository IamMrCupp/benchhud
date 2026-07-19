"""Application dark theme.

A single QSS stylesheet applied at startup so the HUD reads as one dark
bench/stream app rather than default-Qt light. Teal accent deliberately echoes
the meter's cyan LCD, which stays light on purpose — it looks like an instrument
screen glowing against dark equipment.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

DARK_QSS = """
QWidget { background-color: #23262d; color: #d8dee9; }
QMainWindow, QDialog { background-color: #23262d; }

QMenuBar { background-color: #1b1e24; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: #333842; }
QMenu { background-color: #2b2f37; border: 1px solid #3a3f4a; }
QMenu::item { padding: 5px 22px; }
QMenu::item:selected { background: #3a6b78; }

QDockWidget { titlebar-close-icon: none; }
QDockWidget::title {
    background: #1b1e24; padding: 5px 8px; border: 1px solid #3a3f4a;
}

QPushButton {
    background-color: #2f343d; border: 1px solid #3a3f4a;
    border-radius: 4px; padding: 4px 10px;
}
QPushButton:hover { background-color: #3a4049; }
QPushButton:pressed { background-color: #4a9db5; color: #10151a; }

QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #2b2f37; border: 1px solid #3a3f4a;
    border-radius: 4px; padding: 3px 6px;
}
QComboBox QAbstractItemView {
    background: #2b2f37; selection-background-color: #3a6b78;
}

QTabWidget::pane { border: 1px solid #3a3f4a; }
QTabBar::tab {
    background: #23262d; padding: 6px 12px; border: 1px solid #3a3f4a;
}
QTabBar::tab:selected { background: #2f343d; }

QTableWidget { background-color: #1f2229; gridline-color: #3a3f4a; }
QHeaderView::section { background-color: #2b2f37; border: none; padding: 4px; }

QStatusBar { background: #1b1e24; }

QScrollBar:vertical { background: #23262d; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #3a3f4a; border-radius: 6px; min-height: 24px; }
QScrollBar:horizontal { background: #23262d; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #3a3f4a; border-radius: 6px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }

QSlider::groove:horizontal { height: 4px; background: #3a3f4a; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #4a9db5; width: 12px; border-radius: 6px; margin: -5px 0;
}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the dark theme to the application."""
    app.setStyleSheet(DARK_QSS)
