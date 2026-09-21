"""Thermal panel — displays a colorized thermal frame + hotspot readout.

Render-only: hand it a per-pixel °C array via :meth:`update_temps` (produced on
the bench by ``thermal.convert.y16_to_celsius``) and it shows a colormapped
heatmap plus the hottest-point temperature. Shows 'No signal' until fed — the
default app does not synthesize a fake thermal feed (a heatmap with a temperature
label could be mistaken for a real reading, unlike the obvious scope test pattern).
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from benchhud.overlay.composite import colorize
from benchhud.shell.panel import Panel
from benchhud.shell.qt_image import bgr_to_qimage
from benchhud.thermal.analyze import hottest


class ThermalPanel(Panel):
    panel_id = "thermal"
    panel_title = "Thermal"

    def __init__(
        self,
        tmin: float = 20.0,
        tmax: float = 100.0,
        colormap: int = cv2.COLORMAP_INFERNO,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tmin = tmin
        self._tmax = tmax
        self._colormap = colormap
        self._source_pixmap: QPixmap | None = None
        self._last_temps: np.ndarray | None = None

        self._view = QLabel("No signal")
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setMinimumSize(160, 120)
        self._readout = QLabel("max: —")
        self._readout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view, stretch=1)
        layout.addWidget(self._readout)

    @property
    def has_frame(self) -> bool:
        return self._source_pixmap is not None

    @property
    def last_temps(self) -> np.ndarray | None:
        """The most recent °C array (for capture-to-job)."""
        return self._last_temps

    @property
    def readout_text(self) -> str:
        return self._readout.text()

    def update_temps(self, temps: np.ndarray | None) -> None:
        """Show a °C array as a heatmap + hotspot readout. ``None`` is ignored."""
        if temps is None:
            return
        self._last_temps = temps
        heat = colorize(temps, self._tmin, self._tmax, self._colormap)
        self._source_pixmap = QPixmap.fromImage(bgr_to_qimage(heat))
        self._render()

        spot = hottest(temps)
        self._readout.setText(f"max: {spot.value:.1f}°C @ ({spot.x}, {spot.y})")

    def clear(self) -> None:
        """Drop the last reading and go back to 'No signal'.

        Called when the thermal camera is lost, so a frozen heatmap can't pass
        for a live one on the panel or in the composite.
        """
        self._last_temps = None
        self._source_pixmap = None
        self._view.clear()
        self._view.setText("No signal")
        self._readout.setText("max: —")

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
