"""The mouse wheel scrolls a dock, not the controls it passes over (#320).

Every dock sits in a scroll area. A number box, dropdown or slider takes the
wheel whenever the pointer is over it, so scrolling a dock stalled on them or
quietly changed a setting. With :class:`WheelGuard` installed, those controls
inside a scroll area only take the wheel once they've been clicked into;
otherwise it goes on to the scroll area. Lists and tables still scroll their
own contents.
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


class WheelGuard(QObject):
    """An application event filter: see the module docstring."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Polish and guarded(watched):
            # Not focusable by the wheel, or the wheel would focus it and then turn it.
            assert isinstance(watched, QWidget)
            if watched.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                watched.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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


def install(parent: QObject) -> WheelGuard | None:
    """Install a guard on the running application, owned by ``parent``.

    Qt drops the filter when ``parent`` is destroyed.
    """
    app = QApplication.instance()
    if app is None:
        return None
    guard = WheelGuard(parent)
    app.installEventFilter(guard)
    return guard
