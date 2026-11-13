"""The mouse wheel scrolls a dock, not the controls it passes over (#320).

Every dock sits in a scroll area. A number box, dropdown or slider takes the
wheel whenever the pointer is over it, so scrolling a dock stalled on them or
quietly changed a setting. With :class:`WheelGuard` installed, those controls
inside a scroll area only take the wheel once they've been clicked into;
otherwise it goes on to the scroll area. Lists and tables still scroll their
own contents.

The guard filters the controls themselves, one scroll area at a time. It must
not go on the ``QApplication``: PySide then has to wrap every object in the
process that receives an event, Qt's own and the desktop's plugins' included,
and 0.12.0 segfaulted in exactly that on KDE Wayland (#361).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QScrollBar,
    QWidget,
)


def scroll_area(widget: QObject) -> QAbstractScrollArea | None:
    """The scroll area a guarded control sits in, or None if it isn't guarded."""
    if not isinstance(widget, (QAbstractSpinBox, QComboBox, QAbstractSlider)):
        return None
    if isinstance(widget, QScrollBar):
        return None  # a scroll bar is what the wheel is for
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


def guarded(widget: QObject) -> bool:
    """Whether ``widget`` is a control the wheel shouldn't turn in passing."""
    return scroll_area(widget) is not None


_CONTROLS = (QAbstractSpinBox, QComboBox, QAbstractSlider)


class WheelGuard(QObject):
    """Filters the controls inside one scroll area: see the module docstring."""

    def __init__(self, area: QAbstractScrollArea) -> None:
        super().__init__(area)
        self._area = area
        area.installEventFilter(self)  # to sweep again whenever it's shown
        self.sweep()

    def sweep(self) -> int:
        """Guard every control now in the area. Returns how many it guards.

        Safe to repeat: Qt keeps one copy of a filter per object.
        """
        count = 0
        for kind in _CONTROLS:
            for control in self._area.findChildren(kind):
                if not guarded(control):
                    continue
                control.installEventFilter(self)
                # Not focusable by the wheel, or the wheel would focus it and then turn it.
                if control.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                    control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                count += 1
        return count

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Show and watched is self._area:
            self.sweep()  # picks up controls a panel built after it was docked
        elif kind == QEvent.Type.Wheel:
            area = scroll_area(watched)
            if area is not None and isinstance(event, QWheelEvent):
                assert isinstance(watched, QWidget)
                if not watched.hasFocus():
                    _forward(event, area)
                    return True
        return False


def _forward(event: QWheelEvent, area: QAbstractScrollArea) -> None:
    """Re-send ``event`` to ``area``'s viewport.

    Handing it on directly, rather than ignoring it, because Qt only passes a
    real wheel event up the parents, never one sent from code. And as a new
    event: Qt drops the original if it's delivered a second time.
    """
    viewport = area.viewport()
    position = QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint()))
    copy = QWheelEvent(
        position,
        event.globalPosition(),
        event.pixelDelta(),
        event.angleDelta(),
        event.buttons(),
        event.modifiers(),
        event.phase(),
        event.inverted(),
        event.source(),
    )
    QApplication.sendEvent(viewport, copy)


def install(area: QAbstractScrollArea) -> WheelGuard:
    """Guard the controls in ``area``. Qt drops the guard with the area."""
    return WheelGuard(area)
