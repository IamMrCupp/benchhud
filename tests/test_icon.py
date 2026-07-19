"""Tests for the bundled application icon."""

from __future__ import annotations

from benchhud.shell.icon import app_icon
from benchhud.shell.main_window import MainWindow


def test_app_icon_loads(qtbot) -> None:
    icon = app_icon()
    assert not icon.isNull()
    assert icon.availableSizes()  # the PNG decoded to at least one size


def test_window_has_icon(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window.windowIcon().isNull()
