"""Synthetic signal generator for Demo mode.

Produces plausible-but-fake thermal frames and instrument telemetry so the whole
HUD animates with no hardware — for development and stream teasers. Qt-free and
deterministic (advances on :meth:`step`), so it's unit-tested. Demo mode is
always clearly labelled in the UI; this is obviously-synthetic data, never real.
"""

from __future__ import annotations

import numpy as np

from benchhud.instruments.owon_spm8104 import DmmReading
from benchhud.instruments.telemetry import TelemetrySnapshot


class DemoSignal:
    """Deterministic synthetic thermal + telemetry source."""

    def __init__(self, width: int = 256, height: int = 192) -> None:
        self.width = width
        self.height = height
        self._tick = 0

    def step(self) -> None:
        self._tick += 1

    def thermal(self) -> np.ndarray:
        """A °C field: ~28°C ambient with a hotspot roaming in a circle."""
        yy, xx = np.mgrid[0 : self.height, 0 : self.width].astype(np.float32)
        phase = self._tick * 0.05
        cx = self.width * 0.5 + self.width * 0.25 * np.cos(phase)
        cy = self.height * 0.5 + self.height * 0.25 * np.sin(phase)
        sigma = self.width * 0.12
        bloom = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma**2)))
        return (28.0 + 57.0 * bloom).astype(np.float32)

    def telemetry(self) -> TelemetrySnapshot:
        """A snapshot: ~12 V rail, oscillating current (periodic draw spikes)."""
        phase = self._tick * 0.1
        voltage = 12.0 + 0.05 * float(np.sin(phase))
        spike = 0.6 if (self._tick // 40) % 5 == 0 else 0.0
        current = max(0.0, 0.30 + 0.30 * float(np.sin(phase * 0.7)) ** 2 + spike)
        dmm = DmmReading(mode="VDC", value=round(voltage, 2), status=None, ranging="Auto", raw="")
        return TelemetrySnapshot(dmm=dmm, voltage=voltage, current=current)
