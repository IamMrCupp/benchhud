"""Headless smoke tests for the HUD shell window (offscreen via pytest-qt)."""

from __future__ import annotations

from benchhud.shell.main_window import MainWindow


def test_main_window_constructs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.windowTitle() == "benchhud"
    assert window.centralWidget() is not None


def test_main_window_shows(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()
