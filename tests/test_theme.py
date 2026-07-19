"""Tests for the dark theme."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from benchhud.shell.theme import DARK_QSS, apply_theme


def test_apply_theme_sets_stylesheet(qtbot) -> None:
    app = QApplication.instance()
    assert app is not None
    apply_theme(app)
    assert app.styleSheet() == DARK_QSS
    assert "background-color" in app.styleSheet()
    # Reset so other tests aren't affected by the global stylesheet.
    app.setStyleSheet("")
