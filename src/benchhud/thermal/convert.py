"""Convert Sipeed T256s raw Y16 pixels to temperature in °C.

Per Sipeed's T256s User Guide: the raw 14-bit Y16 value is the temperature in
**Kelvin x 64**, so ``celsius = raw / 64.0 - 273.15``. This applies to the *raw*
Y16 stream only — the MJPEG stream is already colour-mapped and cannot be
converted back. On the bench the camera needs ~2 min to reach thermal
equilibrium, and low-emissivity surfaces (bare metal) need tape / matte paint
for accurate readings.

Source: https://wiki.sipeed.com/hardware/en/ThermalCam/T256s/UG.html

The conversion is pure arithmetic, so it's unit-tested here with exact values;
validating it against a known reference temperature needs the real camera.
"""

from __future__ import annotations

import numpy as np

# Raw Y16 encodes Kelvin x KELVIN_SCALE.
KELVIN_SCALE = 64.0
KELVIN_OFFSET = 273.15


def y16_to_celsius(raw: np.ndarray) -> np.ndarray:
    """Convert a raw Y16 array to a float32 °C array (same shape)."""
    return raw.astype(np.float32) / KELVIN_SCALE - KELVIN_OFFSET


def celsius_to_y16(celsius: np.ndarray) -> np.ndarray:
    """Inverse of :func:`y16_to_celsius` (uint16 raw). Handy for tests/synthesis."""
    kelvin_scaled = (np.asarray(celsius, dtype=np.float32) + KELVIN_OFFSET) * KELVIN_SCALE
    return np.rint(kelvin_scaled).astype(np.uint16)
