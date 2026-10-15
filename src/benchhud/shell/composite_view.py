"""The central composite view — the HUD's main image.

Shows the scope frame with the thermal overlay applied (via
:func:`benchhud.overlay.composite.compose`), which is also what gets pushed to
the virtual camera. Degrades gracefully: scope-only or thermal-only when just
one feed is live, and a hint when neither is.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from benchhud.shell.qt_image import bgr_to_qimage

_PLACEHOLDER = (
    "benchhud — composite / preview area\n\n"
    "Start a camera (or View → Demo mode) to see the scope + thermal composite."
)


class CompositeView(QWidget):
    """Displays the composited BGR frame, scaled to fit."""

    #: A click on the picture, in the frame's own pixels (thermal calibration, #243).
    clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_pixmap: QPixmap | None = None

        self._label = QLabel(_PLACEHOLDER)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setMinimumWidth(1)  # never force a wide window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

    @property
    def has_frame(self) -> bool:
        return self._source_pixmap is not None

    def show_frame(self, frame: np.ndarray | None) -> None:
        """Display a BGR frame. ``None`` keeps whatever is already shown."""
        if frame is None:
            return
        self._source_pixmap = QPixmap.fromImage(bgr_to_qimage(frame))
        self._render()

    def _render(self) -> None:
        if self._source_pixmap is None:
            return
        target = self._label.size()
        if target.isEmpty():
            self._label.setPixmap(self._source_pixmap)
        else:
            self._label.setPixmap(
                self._source_pixmap.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render()

    def frame_point(self, pos: QPointF) -> tuple[float, float] | None:
        """Where a point in this widget falls on the frame, or None if off it."""
        shown = self._label.pixmap()
        if self._source_pixmap is None or shown is None or shown.isNull():
            return None
        local = self._label.mapFrom(self, pos.toPoint())
        left = (self._label.width() - shown.width()) / 2.0
        top = (self._label.height() - shown.height()) / 2.0
        x, y = local.x() - left, local.y() - top
        if not (0 <= x < shown.width() and 0 <= y < shown.height()):
            return None
        source = self._source_pixmap
        return x * source.width() / shown.width(), y * source.height() / shown.height()

    def set_picking(self, picking: bool) -> None:
        """A crosshair while a click is wanted."""
        cursor = Qt.CursorShape.CrossCursor if picking else Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        point = self.frame_point(event.position())
        if point is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(*point)
        super().mousePressEvent(event)
