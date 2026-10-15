"""Burn the live V/A/W power trend into the composited (streamed) frame.

The meter dock's trend chart only exists in Qt, so OBS never saw it. This
paints an on-stream copy that goes through the same single point as the meter
readout (``MainWindow._show_composite``), so the preview and the virtual camera
stay identical. It's PII-free by nature: electrical readings only.

Layered like :mod:`benchhud.overlay.telemetry`:

* :func:`build_graph_model`: pure. It turns :class:`PowerSample` history into
  normalised polylines plus the latest values for the legend. No Qt.
* :func:`render_graph_image`: Qt. It paints the panel to an ARGB image.
* :func:`~benchhud.overlay.telemetry.blit_rgba`: composites it onto the frame.

**Each line gets its own vertical range.** The dock plots V, A and W on one
shared axis, which is fine up close, but at stream size a 0.2 A trace next to a
5 V one is a flat line, and the current trace is usually the one worth
watching (a short pulling current). The legend carries the live value of each
series, so the absolute numbers stay on screen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPolygonF

from benchhud.config import GraphOverlayConfig
from benchhud.instruments.power_history import PowerSample
from benchhud.overlay.telemetry import _qimage_to_bgra, auto_scale, blit_rgba
from benchhud.palette import SeriesPalette, palette

#: (legend label, unit), in the dock chart's order: voltage, current, power.
SERIES = (("V", "V"), ("A", "A"), ("W", "W"))


def _series_colours(colours: SeriesPalette) -> tuple[str, str, str]:
    return (colours.voltage, colours.current, colours.power)


@dataclass(frozen=True)
class GraphModel:
    """What to draw: one polyline per series in unit coordinates, plus live values.

    Each polyline is ``[(x, y), …]`` with x in 0..1 across the time window and
    y in 0..1 bottom-to-top within that series' own range.
    """

    lines: tuple[tuple[tuple[float, float], ...], ...]
    latest: tuple[float, float, float] | None  # V, A, W; None before the first reading
    window_seconds: float
    #: The known-good power-on curve (#263), on the *current* trace's scale.
    reference: tuple[tuple[float, float], ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any(len(line) >= 2 for line in self.lines)


def _normalise(values: np.ndarray) -> np.ndarray:
    low, high = float(values.min()), float(values.max())
    low = min(low, 0.0)  # anchor at zero, so a steady 5 V sits high, not mid-panel
    span = high - low
    if span <= 1e-9:
        return np.zeros_like(values)
    return (values - low) / span


def build_graph_model(
    samples: Sequence[PowerSample],
    window_seconds: float,
    reference: Sequence[tuple[float, float]] = (),
) -> GraphModel:
    """Turn the most recent ``window_seconds`` of history into polylines (pure).

    ``reference`` is a known-good current curve as (time, amps) on the samples'
    clock (#263). It shares the current trace's scale, so a board that stalls
    below it reads as below it.
    """
    window = max(1.0, window_seconds)
    if not samples:
        return GraphModel(lines=((), (), ()), latest=None, window_seconds=window)
    t_latest = samples[-1].t
    recent = [s for s in samples if s.t >= t_latest - window]
    last = samples[-1]
    latest = (last.voltage, last.current, last.power)
    if len(recent) < 2:
        return GraphModel(lines=((), (), ()), latest=latest, window_seconds=window)

    t = np.array([s.t for s in recent], dtype=np.float64)
    # Right edge is "now". Early on, less history than the window just starts
    # partway across instead of being stretched to fill it.
    x = (t - (t_latest - window)) / window
    columns = (
        np.array([s.voltage for s in recent], dtype=np.float64),
        np.array([s.current for s in recent], dtype=np.float64),
        np.array([s.power for s in recent], dtype=np.float64),
    )
    start = t_latest - window
    ref = [(t, a) for t, a in reference if start <= t <= t_latest]
    if len(ref) < 2:
        lines = tuple(
            tuple(zip(x.tolist(), _normalise(values).tolist(), strict=True)) for values in columns
        )
        return GraphModel(lines=lines, latest=latest, window_seconds=window)
    # Normalise the current and the good curve together: one scale for both.
    ref_amps = np.array([a for _, a in ref], dtype=np.float64)
    both = _normalise(np.concatenate([columns[1], ref_amps]))
    current, ref_y = both[: len(columns[1])], both[len(columns[1]) :]
    normalised = (_normalise(columns[0]), current, _normalise(columns[2]))
    lines = tuple(tuple(zip(x.tolist(), values.tolist(), strict=True)) for values in normalised)
    ref_x = [(t - start) / window for t, _ in ref]
    reference_line = tuple(zip(ref_x, ref_y.tolist(), strict=True))
    return GraphModel(lines=lines, latest=latest, window_seconds=window, reference=reference_line)


# --- rendering (Qt) ------------------------------------------------------

_PANEL_BG = QColor(24, 28, 38, 225)  # the dock chart's dark theme, lightly translucent
_PANEL_BORDER = "#4f97a8"  # the meter overlay's border, so the two read as a set
_GRID = QColor(255, 255, 255, 38)
_LABEL_FG = "#d7e3ea"
_REFERENCE = "#8a93a3"  # the known-good curve, as in the Meter's chart

_PLOT_W = 320
_PLOT_H = 110
_LEGEND_PX = 13
_PAD = 8
_RADIUS = 5


def _format(value: float | None, unit: str) -> str:
    if value is None:
        return "—"  # the meter overlay's placeholder, so the two read the same
    return f"{value:.3f} {unit}" if unit == "A" else f"{value:.2f} {unit}"


#: Shown in the plot until there's enough history to draw a line. An enabled
#: overlay always draws its panel, like the meter's "—" cells, so the operator
#: can see it's on (#187).
WAITING = "waiting for readings"


def _legend_texts(latest: tuple[float, float, float] | None) -> list[str]:
    values = latest or (None, None, None)
    return [
        f"{label} {_format(value, unit)}"
        for (label, unit), value in zip(SERIES, values, strict=True)
    ]


def _legend_font(px: int) -> QFont:
    font = QFont()
    font.setPixelSize(px)
    font.setBold(True)
    return font


def _fit_legend_font(texts: list[str], px: int, gap: int, available: int) -> QFont:
    """The legend font at ``px``, shrunk until the whole legend fits ``available``."""
    while True:
        font = _legend_font(px)
        metrics = QFontMetrics(font)
        needed = sum(metrics.horizontalAdvance(t) for t in texts) + gap * (len(texts) - 1)
        if needed <= available or px <= 7:
            return font
        px -= 1


def render_graph_image(
    model: GraphModel,
    scale: float = 1.0,
    colours: SeriesPalette | None = None,
    *,
    width: int | None = None,
) -> QImage:
    """Paint the trend panel to an ARGB image. With no history it's an empty, labelled panel.

    ``width`` fixes the panel's outer width, so it can line up with the meter
    readout it's stacked against (#196). The plot stretches to fill it and the
    legend shrinks if it would overflow. Height always follows ``scale``.
    """
    line_colours = _series_colours(colours or palette())
    factor = max(0.3, scale)
    pad = max(3, round(_PAD * factor))
    natural_w = round(_PLOT_W * factor)
    plot_w = max(60, (width - 2 * pad) if width is not None else natural_w)
    plot_h = max(24, round(_PLOT_H * factor))
    texts = _legend_texts(model.latest)
    legend_font = _fit_legend_font(texts, max(7, round(_LEGEND_PX * factor)), 2 * pad, plot_w)
    legend_h = QFontMetrics(legend_font).height()
    radius = max(2, round(_RADIUS * factor))

    width = plot_w + 2 * pad
    height = legend_h + plot_h + 3 * pad
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        painter.setPen(QPen(QColor(_PANEL_BORDER), 1))
        painter.setBrush(_PANEL_BG)
        painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), radius, radius)

        # Legend: a colour chip and the live value of each series, left to right.
        painter.setFont(legend_font)
        metrics = QFontMetrics(legend_font)
        x = float(pad)
        for colour, text in zip(line_colours, texts, strict=True):
            painter.setPen(QColor(colour))
            painter.drawText(
                QRectF(x, pad, plot_w, legend_h), int(Qt.AlignmentFlag.AlignLeft), text
            )
            x += metrics.horizontalAdvance(text) + 2 * pad

        plot = QRectF(pad, legend_h + 2 * pad, plot_w, plot_h)
        painter.setPen(QPen(_GRID, 1))
        for i in range(1, 4):  # quarter lines, enough to read a trend by
            y = plot.top() + plot.height() * i / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        if model.is_empty:
            painter.setPen(QColor(_LABEL_FG))
            painter.drawText(plot, int(Qt.AlignmentFlag.AlignCenter), WAITING)

        line_w = max(1.0, 2.0 * factor)
        if len(model.reference) >= 2:
            # The known-good board, dashed and quiet, under the live traces.
            pen = QPen(QColor(_REFERENCE), line_w, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(plot.left() + px * plot.width(), plot.bottom() - py * plot.height())
                        for px, py in model.reference
                    ]
                )
            )
        for colour, line in zip(line_colours, model.lines, strict=True):
            if len(line) < 2:
                continue
            polygon = QPolygonF(
                [
                    QPointF(plot.left() + px * plot.width(), plot.bottom() - py * plot.height())
                    for px, py in line
                ]
            )
            painter.setPen(QPen(QColor(colour), line_w))
            painter.drawPolyline(polygon)
    finally:
        painter.end()
    return image


def draw_graph_overlay(
    frame: np.ndarray,
    samples: Sequence[PowerSample],
    config: GraphOverlayConfig,
    *,
    stack: int = 0,
    width: int | None = None,
    margin: int | None = None,
    colours: SeriesPalette | None = None,
    reference: Sequence[tuple[float, float]] = (),
) -> np.ndarray:
    """Render the trend and composite it onto ``frame``.

    Always draws when called (the caller checks ``enabled``). With no readings
    it's an empty panel with "—" values, so an enabled overlay is never invisible.

    ``stack`` pushes the panel that many pixels away from its corner's
    horizontal edge, so it can sit above or below the meter readout in the same
    corner instead of covering it. ``width`` and ``margin``, when stacked, are
    the readout's, so the two panels share both edges instead of the graph
    sticking out (#196).
    """
    model = build_graph_model(samples, config.window_seconds, reference)
    scale = auto_scale(frame.shape[1]) * max(0.1, config.scale)
    image = render_graph_image(model, scale, colours, width=width)
    if image.isNull():
        return frame
    if margin is None:
        margin = max(4, round(16 * scale))
    return blit_rgba(
        frame, _qimage_to_bgra(image), config.corner, margin, opacity=config.opacity, stack=stack
    )
