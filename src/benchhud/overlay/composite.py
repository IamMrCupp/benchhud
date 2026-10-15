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
    OUTLINE = "outline"


#: The auto range spans at least this much, so a cold board isn't all noise.
MIN_SPAN_C = 5.0


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
    # Follow the board: low end at ambient, high end at the hottest point (#244).
    auto_range: bool = True
    scale_bar: bool = True  # the colour scale in the corner


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


def auto_range(temps: np.ndarray) -> tuple[float, float]:
    """Ambient (the median) to the hottest point, at least MIN_SPAN_C wide."""
    low = float(np.median(temps))
    return low, max(float(temps.max()), low + MIN_SPAN_C)


class RangeTracker:
    """Smooths the auto range over frames, so the palette doesn't flicker."""

    def __init__(self, smoothing: float = 0.15) -> None:
        self._smoothing = smoothing
        self._range: tuple[float, float] | None = None

    def update(self, temps: np.ndarray) -> tuple[float, float]:
        low, high = auto_range(temps)
        if self._range is not None:
            k = self._smoothing
            low = self._range[0] + k * (low - self._range[0])
            # Rise at once, so a hot spot is never clipped; settle slowly.
            high = high if high > self._range[1] else self._range[1] + k * (high - self._range[1])
        high = max(high, low + MIN_SPAN_C)
        self._range = (low, high)
        return self._range


def heat_blend(
    scope: np.ndarray,
    temps: np.ndarray,
    tmin: float,
    tmax: float,
    colormap: int,
    alpha: float,
) -> np.ndarray:
    """Colour only what's warm: transparent at ``tmin``, ``alpha`` at ``tmax``.

    The old blend laid the heat map over the whole frame, so a board near
    ambient was dimmed and muddied everywhere. Here cool areas show the scope
    untouched.
    """
    if tmax <= tmin:
        return scope.copy()
    weight = np.clip((temps - tmin) / (tmax - tmin), 0.0, 1.0) * alpha
    heat = colorize(temps, tmin, tmax, colormap).astype(np.float32)
    weight = weight[..., None]
    out = scope.astype(np.float32) * (1.0 - weight) + heat * weight
    return out.astype(np.uint8)


def _colormap_colour(fraction: float, colormap: int) -> tuple[int, int, int]:
    value = np.array([[int(np.clip(fraction, 0, 1) * 255)]], dtype=np.uint8)
    b, g, r = cv2.applyColorMap(value, colormap)[0, 0]
    return int(b), int(g), int(r)


def draw_outlines(
    scope: np.ndarray,
    temps: np.ndarray,
    tmin: float,
    tmax: float,
    colormap: int,
    levels: int = 4,
) -> np.ndarray:
    """Isotherms: the outline of everything above a few levels, over a clear scope."""
    out = scope.copy()
    if tmax <= tmin:
        return out
    thickness = max(1, round(scope.shape[1] / 640))
    for i in range(1, levels + 1):
        fraction = i / (levels + 1)
        level = tmin + fraction * (tmax - tmin)
        mask = (temps >= level).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, contours, -1, _colormap_colour(fraction, colormap), thickness)
    spot = hottest(temps)
    if spot.value > tmin:
        out = draw_marker(
            out, spot.x, spot.y, f"{spot.value:.1f}C", _colormap_colour(1.0, colormap)
        )
    return out


def draw_scale_bar(frame: np.ndarray, tmin: float, tmax: float, colormap: int) -> np.ndarray:
    """A small colour scale with its min and max, top left, so colours mean °C."""
    out = frame.copy()
    height, width = frame.shape[:2]
    unit = max(1.0, width / 1280)
    bar_w, bar_h = round(14 * unit), round(120 * unit)
    x, y = round(16 * unit), round(16 * unit)
    if x + bar_w + 70 * unit > width or y + bar_h + 8 > height:
        return out  # too small a frame to label
    ramp = np.linspace(255, 0, bar_h).astype(np.uint8)[:, None].repeat(bar_w, axis=1)
    out[y : y + bar_h, x : x + bar_w] = cv2.applyColorMap(ramp, colormap)
    cv2.rectangle(out, (x - 1, y - 1), (x + bar_w, y + bar_h), (230, 230, 230), 1)
    scale = 0.45 * unit
    for text, ty in ((f"{tmax:.0f}C", y + round(10 * unit)), (f"{tmin:.0f}C", y + bar_h)):
        org = (x + bar_w + round(6 * unit), ty)
        cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(
            out, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (240, 240, 240), 1, cv2.LINE_AA
        )
    return out


def compose(
    scope: np.ndarray,
    temps: np.ndarray,
    transform: RegistrationTransform,
    settings: OverlaySettings,
) -> np.ndarray:
    """Register the thermal °C array to the scope and apply the overlay mode.

    ``settings.tmin`` / ``tmax`` are the range to use; with auto range the
    caller fills them in from a :class:`RangeTracker`.
    """
    dst_size = (scope.shape[1], scope.shape[0])
    warped = transform.warp(temps.astype(np.float32), dst_size)
    lo, hi = settings.tmin, settings.tmax

    if settings.mode is OverlayMode.BLEND:
        out = heat_blend(scope, warped, lo, hi, settings.colormap, settings.alpha)
    elif settings.mode is OverlayMode.OUTLINE:
        out = draw_outlines(scope, warped, lo, hi, settings.colormap)
    elif settings.mode is OverlayMode.TINT:
        return tint_above(scope, warped, settings.threshold, settings.tint_color, settings.alpha)
    elif settings.mode is OverlayMode.MARKER:
        spot = hottest(warped)
        return draw_marker(scope, spot.x, spot.y, f"{spot.value:.1f}C", settings.marker_color)
    else:
        raise ValueError(f"unknown overlay mode: {settings.mode!r}")
    return draw_scale_bar(out, lo, hi, settings.colormap) if settings.scale_bar else out
