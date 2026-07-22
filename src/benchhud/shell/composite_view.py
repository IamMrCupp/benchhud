"""The central composite view — the HUD's main image.

Shows the scope frame with the thermal overlay applied (via
:func:`benchhud.overlay.composite.compose`), which is also what gets pushed to
the virtual camera. Degrades gracefully: scope-only or thermal-only when just
one feed is live, and a hint when neither is.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from benchhud.shell.qt_image import bgr_to_qimage

_PLACEHOLDER = (
    "benchhud — composite / preview area\n\n"
    "Start a camera (or View → Demo mode) to see the scope + thermal composite."
)


class CompositeView(QWidget):
    """Displays the composited BGR frame, scaled to fit."""

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
