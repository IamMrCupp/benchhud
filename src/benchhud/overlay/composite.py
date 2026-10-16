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
#: Sensor noise is about a degree; at 5 °C it still swept the palette (#278).
MIN_SPAN_C = 10.0


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
    radius: int | None = None,
) -> np.ndarray:
    """Mark ``(x, y)`` on a copy of ``scope``: a ring, a crosshair and a label.

    Sized to the frame (#271): a fixed 8 px ring was a speck on a 1080p scope.
    Every stroke has a dark edge and the label a dark box, so it reads on any
    board, bright or yellow.
    """
    out = scope.copy()
    unit = max(1.0, scope.shape[1] / 640)
    r = radius if radius is not None else round(10 * unit)
    thick = max(2, round(2 * unit))
    edge = (0, 0, 0)
    tick = r + round(6 * unit)
    for colour, width in ((edge, thick + max(2, round(unit))), (color, thick)):
        cv2.circle(out, (x, y), r, colour, width, cv2.LINE_AA)
        # Crosshair: four ticks outside the ring, pointing at the exact pixel.
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            start = (x + dx * (r - round(3 * unit)), y + dy * (r - round(3 * unit)))
            end = (x + dx * tick, y + dy * tick)
            cv2.line(out, start, end, colour, width, cv2.LINE_AA)
    if label:
        font, scale = cv2.FONT_HERSHEY_SIMPLEX, 0.55 * unit
        weight = max(1, round(1.5 * unit))
        (tw, th), base = cv2.getTextSize(label, font, scale, weight)
        pad = round(4 * unit)
        lx = min(x + tick + pad, out.shape[1] - tw - 2 * pad)
        ly = max(y - pad, th + 2 * pad)
        box_tl = (max(0, lx - pad), ly - th - pad)
        box_br = (min(out.shape[1] - 1, lx + tw + pad), ly + base + pad)
        region = out[box_tl[1] : box_br[1], box_tl[0] : box_br[0]]
        if region.size:  # a translucent dark box, so the board shows faintly behind
            out[box_tl[1] : box_br[1], box_tl[0] : box_br[0]] = (region * 0.35).astype(out.dtype)
        cv2.putText(out, label, (lx, ly), font, scale, color, weight, cv2.LINE_AA)
    return out


#: How much hotter a new spot must read before the marker moves to it.
MARKER_HYSTERESIS_C = 0.5
#: How far above the board (the median) a spot must read to be marked at all.
#: Below this the "hottest" pixel is just noise, and the marker hopped (#278).
MARKER_MIN_CONTRAST_C = 3.0


def stands_out(temps: np.ndarray, value: float) -> bool:
    """Whether ``value`` is clearly warmer than the board, not noise."""
    return value - float(np.median(temps)) >= MARKER_MIN_CONTRAST_C


class MarkerTracker:
    """A steady hot-spot marker (#271), in thermal pixels.

    Following the single hottest pixel every frame made the marker hop between
    two spots within a fraction of a degree, or with sensor noise. This holds
    its spot until another is clearly hotter, or it cools away, and glides
    rather than jumps.
    """

    def __init__(self, hysteresis: float = MARKER_HYSTERESIS_C, smoothing: float = 0.5) -> None:
        self._hysteresis = hysteresis
        self._smoothing = smoothing
        self._target: tuple[int, int] | None = None
        self._pos: tuple[float, float] | None = None
        self._value: float | None = None

    def update(self, temps: np.ndarray) -> tuple[float, float, float] | None:
        """(x, y, °C) to mark this frame, or None when nothing stands out."""
        spot = hottest(temps)
        if not stands_out(temps, spot.value):
            self._target = self._pos = self._value = None
            return None
        if self._target is None:
            self._target = (spot.x, spot.y)
        else:
            held = float(temps[self._target[1], self._target[0]])
            if spot.value > held + self._hysteresis:
                self._target = (spot.x, spot.y)
        tx, ty = self._target
        here = float(temps[ty, tx])
        k = self._smoothing
        if self._pos is None:
            self._pos, self._value = (float(tx), float(ty)), here
        else:
            px, py = self._pos
            self._pos = (px + k * (tx - px), py + k * (ty - py))
            self._value = (self._value or here) + k * (here - (self._value or here))
        return self._pos[0], self._pos[1], float(self._value)


def auto_range(temps: np.ndarray, low_percentile: float = 50.0) -> tuple[float, float]:
    """Ambient (the median) to the hottest point, at least MIN_SPAN_C wide.

    The overlay starts at the median so only what's warmer than the board is
    coloured. A full heat map wants a lower floor (``low_percentile=1``): starting
    at the median, half its pixels flickered between black and colour (#278).
    """
    low = float(np.percentile(temps, low_percentile))
    return low, max(float(temps.max()), low + MIN_SPAN_C)


class RangeTracker:
    """Smooths the auto range over frames, so the palette doesn't flicker."""

    def __init__(self, smoothing: float = 0.15, low_percentile: float = 50.0) -> None:
        self._smoothing = smoothing
        self._low_percentile = low_percentile
        self._range: tuple[float, float] | None = None

    def update(self, temps: np.ndarray) -> tuple[float, float]:
        low, high = auto_range(temps, self._low_percentile)
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
    marker: tuple[float, float, float] | None = None,
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
        if marker is not None:
            # A tracked spot (MarkerTracker), in thermal pixels: map it through
            # the registration so calibration still applies.
            src = (temps.shape[1], temps.shape[0])
            mx, my = transform.map_point(marker[0], marker[1], src, dst_size)
            x, y, value = round(mx), round(my), marker[2]
        else:
            spot = hottest(warped)
            if not stands_out(warped, spot.value):
                return scope.copy()  # nothing warmer than the board
            x, y, value = spot.x, spot.y, spot.value
        return draw_marker(scope, x, y, f"{value:.1f}C", settings.marker_color)
    else:
        raise ValueError(f"unknown overlay mode: {settings.mode!r}")
    return draw_scale_bar(out, lo, hi, settings.colormap) if settings.scale_bar else out
