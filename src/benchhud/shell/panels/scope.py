"""Scope panel — displays the live microscope (visible) video feed.

Rendering only: it takes whatever BGR frame it's handed (via :meth:`update_frame`)
and shows it, scaled to fit while preserving aspect ratio. The frame *source*
(threaded reader over a camera or synthetic source) is wired in separately, so
this panel is fully testable off-hardware.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from benchhud.shell.panel import Panel
from benchhud.shell.qt_image import bgr_to_qimage


class ScopePanel(Panel):
    panel_id = "scope"
    panel_title = "Scope"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = QLabel("No signal")
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setMinimumSize(160, 120)
        self._source_pixmap: QPixmap | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

    @property
    def has_frame(self) -> bool:
        return self._source_pixmap is not None

    def update_frame(self, frame: np.ndarray | None) -> None:
        """Show ``frame`` (BGR). ``None`` is ignored (keeps the last frame)."""
        if frame is None:
            return
        self._source_pixmap = QPixmap.fromImage(bgr_to_qimage(frame))
        self._render()

    def _render(self) -> None:
        if self._source_pixmap is None:
            return
        target = self._view.size()
        if target.isEmpty():
            self._view.setPixmap(self._source_pixmap)
        else:
            self._view.setPixmap(
                self._source_pixmap.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render()
