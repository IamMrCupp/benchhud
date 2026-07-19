"""Tests for the View menu (toggle panels + reset layout)."""

from __future__ import annotations

from PySide6.QtWidgets import QMenu

from benchhud.shell.main_window import MainWindow


def test_reset_layout_action_exists(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.reset_layout_action.text() == "Reset layout"


def test_toggle_hides_and_reset_restores_dock(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    dock = window.panels.dock("power")
    assert dock is not None
    assert dock.isVisible()

    dock.toggleViewAction().trigger()  # hide it
    assert not dock.isVisible()

    window.reset_layout()  # restore default arrangement
    assert dock.isVisible()


def test_view_menu_has_a_toggle_per_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    menus = {m.title(): m for m in window.menuBar().findChildren(QMenu)}
    assert "View" in menus
    labels = [a.text() for a in menus["View"].actions() if a.text()]
    assert "Reset layout" in labels
    assert len(labels) >= 6  # 5 panel toggles + reset
