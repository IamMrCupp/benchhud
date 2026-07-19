"""Composite a thermal overlay onto the scope image.

Three overlay modes (plan §2/§4): a semi-transparent heatmap blend, a threshold
tint (glow anything over N°C), and a marker + live-temperature label on the
hottest point. :func:`compose` warps the thermal °C array into scope space (via a
:class:`~benchhud.overlay.transform.RegistrationTransform`) and applies the
selected mode. Everything here is pure array math on BGR frames + °C arrays, so
it's tested with synthetic inputs; only the *transform values* and visual tuning
need the bench.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from benchhud.overlay.transform import RegistrationTransform
from benchhud.thermal.analyze import hottest


class OverlayMode(Enum):
    BLEND = "blend"
    TINT = "tint"
    MARKER = "marker"


@dataclass(frozen=True)
class OverlaySettings:
    mode: OverlayMode = OverlayMode.BLEND
    alpha: float = 0.5
    threshold: float = 60.0  # °C for TINT mode
    tmin: float = 20.0  # °C mapped to the low end of the colormap
    tmax: float = 100.0  # °C mapped to the high end
    colormap: int = cv2.COLORMAP_INFERNO
    tint_color: tuple[int, int, int] = (0, 0, 255)  # BGR red
    marker_color: tuple[int, int, int] = (0, 255, 255)  # BGR yellow


def colorize(
    temps: np.ndarray,
    tmin: float,
    tmax: float,
    colormap: int = cv2.COLORMAP_INFERNO,
) -> np.ndarray:
    """Map a °C array to a BGR heatmap over the range ``[tmin, tmax]``."""
    if tmax <= tmin:
        return np.zeros((*temps.shape, 3), dtype=np.uint8)
    norm = np.clip((temps - tmin) / (tmax - tmin), 0.0, 1.0)
    u8 = (norm * 255).astype(np.uint8)
    return cv2.applyColorMap(u8, colormap)


def blend(base: np.ndarray, overlay: np.ndarray, alpha: float) -> np.ndarray:
    """Alpha-blend ``overlay`` over ``base`` (``alpha`` = overlay weight)."""
    return cv2.addWeighted(base, 1.0 - alpha, overlay, alpha, 0.0)


def tint_above(
    scope: np.ndarray,
    temps: np.ndarray,
    threshold: float,
    color: tuple[int, int, int] = (0, 0, 255),
    alpha: float = 0.5,
) -> np.ndarray:
    """Tint scope pixels whose temperature is at or above ``threshold``."""
    out = scope.copy()
    mask = temps >= threshold
    if mask.any():
        tint = np.empty_like(scope)
        tint[:] = color
        blended = cv2.addWeighted(scope, 1.0 - alpha, tint, alpha, 0.0)
        out[mask] = blended[mask]
    return out


def draw_marker(
    scope: np.ndarray,
    x: int,
    y: int,
    label: str | None = None,
    color: tuple[int, int, int] = (0, 255, 255),
    radius: int = 8,
) -> np.ndarray:
    """Draw a ring (and optional label) at ``(x, y)`` on a copy of ``scope``."""
    out = scope.copy()
    cv2.circle(out, (x, y), radius, color, 2)
    if label:
        cv2.putText(
            out,
            label,
            (x + radius + 2, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return out


def compose(
    scope: np.ndarray,
    temps: np.ndarray,
    transform: RegistrationTransform,
    settings: OverlaySettings,
) -> np.ndarray:
    """Register the thermal °C array to the scope and apply the overlay mode."""
    dst_size = (scope.shape[1], scope.shape[0])
    warped = transform.warp(temps.astype(np.float32), dst_size)

    if settings.mode is OverlayMode.BLEND:
        heat = colorize(warped, settings.tmin, settings.tmax, settings.colormap)
        return blend(scope, heat, settings.alpha)
    if settings.mode is OverlayMode.TINT:
        return tint_above(scope, warped, settings.threshold, settings.tint_color, settings.alpha)
    if settings.mode is OverlayMode.MARKER:
        spot = hottest(warped)
        return draw_marker(scope, spot.x, spot.y, f"{spot.value:.1f}C", settings.marker_color)
    raise ValueError(f"unknown overlay mode: {settings.mode!r}")
