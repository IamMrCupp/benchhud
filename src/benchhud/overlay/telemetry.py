"""Burn the live power/DMM readings into the composited (streamed) frame.

benchhud composites scope+thermal to a virtual camera; this adds an on-stream
copy of the meter dock's readout so viewers see the numbers the operator sees on
the panel. It is the *only* thing that reaches the composite besides the video —
and it is PII-free by nature (electrical readings). The customer/ticket fields
never composite; see :mod:`benchhud.tickets`.

Three layers, split so the logic is testable without a display:

* :func:`build_model` — pure: turn a :class:`MeterReadout` (the meter's formatted
  strings) + config into what to draw. No Qt.
* :func:`render_overlay_image` — Qt: paint the cyan LCD instrument panel with the
  bundled DSEG 7-segment digits (matching the dock) to an RGBA image. cv2 5.x has
  no freetype, so the digits come from Qt's font stack, not ``cv2.putText``.
* :func:`blit_rgba` — pure numpy: alpha-composite the image onto the BGR frame.

:func:`draw_telemetry_overlay` chains them; a hidden/empty readout returns the
frame untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from benchhud.config import TelemetryOverlayConfig
from benchhud.shell.fonts import seven_segment_family

_PLACEHOLDER = "—"


@dataclass(frozen=True)
class MeterReadout:
    """The meter's already-formatted strings, as the PowerPanel displays them.

    Sourced from the panel's own accessors so the overlay is exact parity with
    the dock — no duplicated number formatting, no separate peak-hold state.
    """

    voltage: str = _PLACEHOLDER
    current: str = _PLACEHOLDER
    power: str = _PLACEHOLDER
    max_current: str = _PLACEHOLDER
    dmm_mode: str = "DMM"
    dmm_value: str = _PLACEHOLDER
    dmm_unit: str = ""


@dataclass(frozen=True)
class _Cell:
    caption: str
    value: str


@dataclass(frozen=True)
class OverlayModel:
    """What :func:`render_overlay_image` draws: an optional DMM line + PSU cells."""

    dmm: tuple[str, str, str] | None  # (mode, value, unit)
    cells: tuple[_Cell, ...]

    @property
    def is_empty(self) -> bool:
        return self.dmm is None and not self.cells


def build_model(readout: MeterReadout, config: TelemetryOverlayConfig) -> OverlayModel:
    """Decide what to show from the readout + which sections are enabled (pure)."""
    dmm = (readout.dmm_mode, readout.dmm_value, readout.dmm_unit) if config.show_dmm else None
    cells: tuple[_Cell, ...] = ()
    if config.show_psu:
        cells = (
            _Cell("VOLTAGE (V)", readout.voltage),
            _Cell("CURRENT (A)", readout.current),
            _Cell("POWER (W)", readout.power),
            _Cell("MAX CURRENT (A)", readout.max_current),
        )
    return OverlayModel(dmm=dmm, cells=cells)


# --- rendering (Qt) ------------------------------------------------------

# Palette lifted from the meter dock (power.py) so the on-stream readout matches.
_CELL_BG = "#86cddf"
_CELL_BORDER = "#4f97a8"
_VALUE_FG = "#062028"
_CAPTION_FG = "#0a3a45"
_HEADER_BG = "#f4fbfc"
_LABEL_FG = "#2f6472"

_CAPTION_PX = 12
_VALUE_PX = 24
_DMM_LABEL_PX = 15
_DMM_VALUE_PX = 30
_PAD = 8
_GAP = 6
_RADIUS = 5
# Worst-case value the meter shows: sign, three integer digits, two decimals —
# sizes the cells so segmented digits never clip (as power.py does).
_WIDEST_VALUE = "-000.00"
_WIDEST_CAPTION = "MAX CURRENT (A)"

_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_CENTER = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)


def _digit_font(px: int) -> QFont:
    """The DSEG 7-segment face for numeric readouts (monospace fallback)."""
    font = QFont()
    family = seven_segment_family()
    if family:
        font.setFamily(family)
    font.setPixelSize(px)
    font.setBold(True)
    return font


def _label_font(px: int, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setPixelSize(px)
    font.setBold(bold)
    return font


def render_overlay_image(model: OverlayModel) -> QImage:
    """Paint the readout to an ARGB image (transparent where there's no panel)."""
    caption_fm = QFontMetrics(_label_font(_CAPTION_PX, bold=True))
    value_fm = QFontMetrics(_digit_font(_VALUE_PX))
    dmm_value_fm = QFontMetrics(_digit_font(_DMM_VALUE_PX))
    dmm_label_fm = QFontMetrics(_label_font(_DMM_LABEL_PX))

    cell_content_w = max(
        value_fm.horizontalAdvance(_WIDEST_VALUE),
        caption_fm.horizontalAdvance(_WIDEST_CAPTION),
    )
    cell_w = cell_content_w + 2 * _PAD
    cell_h = caption_fm.height() + value_fm.height() + 3 * _PAD
    grid_w = 2 * cell_w + _GAP if model.cells else 0
    grid_h = 2 * cell_h + _GAP if model.cells else 0

    header_h = 0
    header_w = 0
    if model.dmm is not None:
        mode, value, unit = model.dmm
        header_h = dmm_value_fm.height() + 2 * _PAD
        header_w = (
            dmm_label_fm.horizontalAdvance(mode)
            + dmm_value_fm.horizontalAdvance(value)
            + dmm_label_fm.horizontalAdvance(unit)
            + 4 * _PAD
        )

    panel_w = max(grid_w, header_w)
    panel_h = header_h + (_GAP if header_h and grid_h else 0) + grid_h
    if panel_w == 0 or panel_h == 0:
        return QImage()

    image = QImage(panel_w, panel_h, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        y = 0
        if model.dmm is not None:
            mode, value, unit = model.dmm
            _panel(painter, QRectF(0, 0, panel_w, header_h), _HEADER_BG, _CELL_BORDER)
            inner = QRectF(_PAD, 0, panel_w - 2 * _PAD, header_h)
            painter.setFont(_label_font(_DMM_LABEL_PX))
            painter.setPen(QColor(_LABEL_FG))
            painter.drawText(inner, _ALIGN_LEFT, mode)
            painter.drawText(inner, _ALIGN_RIGHT, unit)
            painter.setFont(_digit_font(_DMM_VALUE_PX))
            painter.setPen(QColor(_VALUE_FG))
            painter.drawText(inner, _ALIGN_CENTER, value)
            y = header_h + _GAP

        for i, cell in enumerate(model.cells):
            col, row = i % 2, i // 2
            x = col * (cell_w + _GAP)
            cy = y + row * (cell_h + _GAP)
            _panel(painter, QRectF(x, cy, cell_w, cell_h), _CELL_BG, _CELL_BORDER)
            painter.setFont(_label_font(_CAPTION_PX, bold=True))
            painter.setPen(QColor(_CAPTION_FG))
            painter.drawText(
                QRectF(x + _PAD, cy + _PAD, cell_w - 2 * _PAD, caption_fm.height()),
                int(Qt.AlignmentFlag.AlignLeft),
                cell.caption,
            )
            painter.setFont(_digit_font(_VALUE_PX))
            painter.setPen(QColor(_VALUE_FG))
            painter.drawText(
                QRectF(x, cy + _PAD + caption_fm.height(), cell_w, value_fm.height() + _PAD),
                _ALIGN_CENTER,
                cell.value,
            )
    finally:
        painter.end()
    return image


def _panel(painter: QPainter, rect: QRectF, fill: str, border: str) -> None:
    painter.setPen(QPen(QColor(border), 1))
    painter.setBrush(QColor(fill))
    painter.drawRoundedRect(rect, _RADIUS, _RADIUS)


def _qimage_to_bgra(image: QImage) -> np.ndarray:
    """Copy an ARGB32 QImage into an ``(h, w, 4)`` BGRA uint8 array."""
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    height, width = image.height(), image.width()
    buffer = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
        (height, image.bytesPerLine())
    )
    # ARGB32 is 0xAARRGGBB; little-endian byte order is B,G,R,A — already BGRA.
    return buffer[:, : width * 4].reshape((height, width, 4)).copy()


# --- compositing (pure numpy) --------------------------------------------


def _corner_origin(corner: str, fw: int, fh: int, ow: int, oh: int, margin: int) -> tuple[int, int]:
    x = fw - ow - margin if "right" in corner else margin
    y = fh - oh - margin if "bottom" in corner else margin
    return max(0, min(x, fw - ow)), max(0, min(y, fh - oh))


def blit_rgba(
    frame: np.ndarray,
    overlay: np.ndarray,
    corner: str = "bottom-left",
    margin: int = 16,
) -> np.ndarray:
    """Alpha-composite a BGRA ``overlay`` onto a BGR ``frame`` at ``corner``.

    Returns a new frame; the input is left untouched. An overlay larger than the
    frame is cropped rather than refused, so the HUD never crashes on a tiny
    preview.
    """
    fh, fw = frame.shape[:2]
    oh, ow = overlay.shape[:2]
    oh, ow = min(oh, fh), min(ow, fw)
    if oh <= 0 or ow <= 0:
        return frame
    overlay = overlay[:oh, :ow]
    x, y = _corner_origin(corner, fw, fh, ow, oh, margin)

    out = frame.copy()
    roi = out[y : y + oh, x : x + ow].astype(np.float32)
    color = overlay[:, :, :3].astype(np.float32)
    alpha = overlay[:, :, 3:4].astype(np.float32) / 255.0
    out[y : y + oh, x : x + ow] = (color * alpha + roi * (1.0 - alpha)).astype(np.uint8)
    return out


def draw_telemetry_overlay(
    frame: np.ndarray,
    readout: MeterReadout,
    config: TelemetryOverlayConfig,
) -> np.ndarray:
    """Render the readout and composite it onto ``frame`` (frame unchanged if hidden)."""
    model = build_model(readout, config)
    if model.is_empty:
        return frame
    image = render_overlay_image(model)
    if image.isNull() or image.width() == 0 or image.height() == 0:
        return frame
    return blit_rgba(frame, _qimage_to_bgra(image), config.corner)
