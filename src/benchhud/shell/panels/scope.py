"""Scope panel — displays the live microscope (visible) video feed.

Rendering only: it takes whatever BGR frame it's handed (via :meth:`update_frame`)
and shows it, scaled to fit while preserving aspect ratio. The frame *source*
(threaded reader over a camera or synthetic source) is wired in separately, so
this panel is fully testable off-hardware.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from benchhud.shell.panel import Panel
from benchhud.shell.qt_image import bgr_to_fitted_pixmap


class ScopePanel(Panel):
    panel_id = "scope"
    panel_title = "Scope"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = QLabel("No signal")
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setMinimumSize(160, 120)
        # The image's size hint is whatever it was last scaled to; don't let it
        # size the panel (#294).
        self._view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._last_frame: np.ndarray | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

    @property
    def has_frame(self) -> bool:
        return self._last_frame is not None

    @property
    def last_frame(self) -> np.ndarray | None:
        """The most recent BGR frame (for capture-to-job)."""
        return self._last_frame

    def update_frame(self, frame: np.ndarray | None) -> None:
        """Show ``frame`` (BGR). ``None`` is ignored (keeps the last frame)."""
        if frame is None:
            return
        self._last_frame = frame
        self._render()

    def _render(self) -> None:
        # A closed or tabbed-away dock draws nothing; showEvent catches it up (#347).
        if self._last_frame is None or not self.isVisible():
            return
        self._view.setPixmap(bgr_to_fitted_pixmap(self._last_frame, self._view.size()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._render()
