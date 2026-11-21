"""Tests for the dark theme."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

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


def test_group_titles_are_small_caps_and_their_contents_are_not(qtbot) -> None:
    """Burnwright's panel headings (#440): a font is inherited, so the body is reset."""
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QGroupBox

    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    upper, mixed = QFont.Capitalization.AllUppercase, QFont.Capitalization.MixedCase

    group = window.power_panel.findChild(QGroupBox)
    assert group.font().capitalization() == upper
    assert all(
        child.font().capitalization() == mixed
        for child in group.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
    )
    assert window.controls_panel.mode_combo.font().capitalization() == mixed


def test_erase_and_write_are_marked_as_destructive(qtbot) -> None:
    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    for panel in (window.flash_panel, window.minipro_panel):
        assert panel.erase_button.property("danger") is True
        assert panel.write_button.property("danger") is True
        assert not panel.read_button.property("danger")
    assert 'QPushButton[danger="true"]' in stylesheet()
