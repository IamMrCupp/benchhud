"""The scope-only and thermal-only feeds, beside the composite (#344).

The composite is the HUD's picture; these let OBS put the cameras where it
likes (stacked, side by side, thermal alone) with a scene each. They carry no
telemetry overlays and no recon layers — nothing sensitive is ever drawn on
them — so they need none of :mod:`benchhud.output.onair`'s filtering.
"""

from __future__ import annotations

import cv2
import numpy as np

from benchhud.overlay.composite import colorize, draw_scale_bar


def thermal_size(height: int, shape: tuple[int, ...] = (192, 256)) -> tuple[int, int]:
    """The thermal feed's ``(width, height)``: the camera's own shape at ``height``.

    ``shape`` is the °C array's ``(rows, cols)``. The bench's camera hands it
    over portrait, so the feed is too — no black bars either side. Even sides,
    which the loopback wants.
    """
    height = max(2, height - height % 2)
    rows, cols = shape[0], shape[1]
    width = max(2, round(height * cols / rows))
    return width - width % 2, height


def fit_frame(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Scale ``frame`` to fit ``size`` without stretching; black bars fill the rest."""
    width, height = size
    src_h, src_w = frame.shape[:2]
    if (src_w, src_h) == (width, height):
        return frame
    scale = min(width / src_w, height / src_h)
    fit_w, fit_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(frame, (fit_w, fit_h), interpolation=interp)
    if (fit_w, fit_h) == (width, height):
        return resized
    out = np.zeros((height, width, 3), dtype=np.uint8)
    left, top = (width - fit_w) // 2, (height - fit_h) // 2
    out[top : top + fit_h, left : left + fit_w] = resized
    return out


def thermal_frame(
    temps: np.ndarray,
    tmin: float,
    tmax: float,
    colormap: int,
    size: tuple[int, int],
    *,
    scale_bar: bool,
) -> np.ndarray:
    """The thermal feed at ``size``: colourised like the composite, scale bar optional.

    Scaled up before the bar is drawn — at the sensor's 256x192 it's too small
    to label, and the bar should be crisp, not upscaled.
    """
    heat = fit_frame(colorize(temps, tmin, tmax, colormap), size)
    return draw_scale_bar(heat, tmin, tmax, colormap) if scale_bar else heat
