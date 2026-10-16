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
from PySide6.QtGui import QPainter, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from benchhud.overlay.composite import RangeTracker, colorize
from benchhud.shell.panel import Panel
from benchhud.shell.qt_image import bgr_to_qimage
from benchhud.thermal.analyze import hottest

PAUSED_SUFFIX = "   (paused)"


def _dimmed(pixmap: QPixmap) -> QPixmap:
    out = QPixmap(pixmap.size())
    out.fill(Qt.GlobalColor.black)
    painter = QPainter(out)
    painter.setOpacity(0.45)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return out


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
        # Follow the board by default (#269): a fixed 20-100 °C scale left a
        # 30 °C board nearly black.
        self._auto = True
        # The panel is a full heat map: start the range at the cool end (#278).
        self._range = RangeTracker(low_percentile=1.0)
        self._source_pixmap: QPixmap | None = None
        self._last_temps: np.ndarray | None = None
        self._paused = False

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
        self._paused = False
        low, high = self._range.update(temps) if self._auto else (self._tmin, self._tmax)
        heat = colorize(temps, low, high, self._colormap)
        self._source_pixmap = QPixmap.fromImage(bgr_to_qimage(heat))
        self._render()

        spot = hottest(temps)
        self._readout.setText(
            f"max: {spot.value:.1f}°C @ ({spot.x}, {spot.y})   scale {low:.0f} to {high:.0f}°C"
        )

    def set_style(self, *, auto_range: bool, tmin: float, tmax: float, colormap: int) -> None:
        """Match the Controls panel: its colour range and colormap (#269)."""
        self._auto = auto_range
        self._tmin, self._tmax = tmin, tmax
        self._colormap = colormap
        if self._last_temps is not None:
            self.update_temps(self._last_temps)

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

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        """Show the last frame dimmed while the camera pauses (#276).

        Thermal cameras stop sending frames while the shutter recalibrates. Blanking
        to "No signal" for each pause made the panel flicker.
        """
        if paused == self._paused:
            return
        self._paused = paused
        if self._source_pixmap is not None:
            text = self._readout.text().removesuffix(PAUSED_SUFFIX)
            self._readout.setText(text + (PAUSED_SUFFIX if paused else ""))
            self._render()

    def _render(self) -> None:
        if self._source_pixmap is None:
            return
        pixmap = self._source_pixmap
        if self._paused:
            pixmap = _dimmed(pixmap)
        target = self._view.size()
        if target.isEmpty():
            self._view.setPixmap(pixmap)
        else:
            self._view.setPixmap(
                pixmap.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render()
