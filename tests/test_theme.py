"""Tests for the dark theme."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from benchhud.shell.theme import DARK_QSS, apply_theme, stylesheet


def test_apply_theme_sets_stylesheet(qtbot) -> None:
    app = QApplication.instance()
    assert app is not None
    apply_theme(app)
    assert app.styleSheet() == stylesheet()
    assert app.styleSheet().startswith(DARK_QSS)
    assert "background-color" in app.styleSheet()
    # Reset so other tests aren't affected by the global stylesheet.
    app.setStyleSheet("")


def test_checkboxes_have_a_visible_box_and_mark(qtbot) -> None:
    """The native box was painted over by the window colour (#286)."""
    from pathlib import Path

    qss = stylesheet()
    assert "QCheckBox::indicator" in qss and "{check}" not in qss
    check = qss.split('image: url("', 1)[1].split('")', 1)[0]
    assert Path(check).is_file()
