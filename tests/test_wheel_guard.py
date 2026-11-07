"""The wheel scrolls a dock, not the controls it passes over (#320)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QListWidget,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from benchhud.shell.wheel_guard import WheelGuard, guarded


def _wheel(widget: QWidget) -> None:
    centre = QPointF(widget.rect().center())
    event = QWheelEvent(
        centre,
        QPointF(widget.mapToGlobal(centre.toPoint())),
        QPoint(0, 0),
        QPoint(0, -120),  # scroll down
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


@pytest.fixture
def dock(qtbot):
    """A tall panel of controls in a short scroll area, like a squeezed dock."""
    guard = WheelGuard()
    QApplication.instance().installEventFilter(guard)
    panel = QWidget()
    layout = QVBoxLayout(panel)
    spin = QDoubleSpinBox()
    spin.setRange(0, 100)
    spin.setValue(50)
    combo = QComboBox()
    combo.addItems(["blend", "tint", "marker"])
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(50)
    # Something else holds the focus, as when you've been typing elsewhere;
    # otherwise Qt focuses the first control on show.
    elsewhere = QLineEdit()
    for widget in (elsewhere, spin, combo, slider):
        layout.addWidget(widget)
    layout.addSpacing(2000)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(panel)
    scroll.resize(300, 200)
    qtbot.addWidget(scroll)
    scroll.show()
    qtbot.waitExposed(scroll)
    elsewhere.setFocus()
    assert not any(w.hasFocus() for w in (spin, combo, slider))
    yield scroll, spin, combo, slider
    QApplication.instance().removeEventFilter(guard)


def test_the_wheel_over_a_control_scrolls_the_dock(dock) -> None:
    scroll, spin, combo, slider = dock
    bar = scroll.verticalScrollBar()
    for widget in (spin, combo, slider):
        before = bar.value()
        _wheel(widget)
        assert bar.value() > before, type(widget).__name__
    assert spin.value() == 50
    assert combo.currentText() == "blend"
    assert slider.value() == 50


def test_the_wheel_cannot_focus_a_control(dock) -> None:
    _, spin, combo, slider = dock
    for widget in (spin, combo, slider):
        # No wheel bit (macOS gives combos and sliders TabFocus; that's fine too).
        assert not widget.focusPolicy().value & 0x4, type(widget).__name__


def test_a_clicked_into_control_takes_the_wheel(dock, monkeypatch) -> None:
    scroll, spin, _, _ = dock
    # Offscreen Qt has no active window to give focus to, so stand in for a click.
    monkeypatch.setattr(spin, "hasFocus", lambda: True)
    before = scroll.verticalScrollBar().value()
    _wheel(spin)
    assert spin.value() == 49
    assert scroll.verticalScrollBar().value() == before


def test_only_controls_inside_a_scroll_area_are_guarded(qtbot) -> None:
    loose = QDoubleSpinBox()
    qtbot.addWidget(loose)
    assert not guarded(loose)
    assert not guarded(QListWidget())
    scroll = QScrollArea()
    qtbot.addWidget(scroll)
    assert not guarded(scroll.verticalScrollBar())


def test_the_window_installs_the_guard(qtbot) -> None:
    from benchhud.config import Config
    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(Config(), job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    assert isinstance(window._wheel_guard, WheelGuard)
