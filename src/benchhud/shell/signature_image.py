"""Draw a power-on signature as a small chart image, for the job (#236).

The curve is current against time, with the known-good curve dashed behind it
when there is one. Plain QPainter, so it needs no chart widget or window.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from benchhud.instruments.signature import Signature, reference_at
from benchhud.palette import palette

_BACKGROUND = QColor("#1f2229")
_GRID = QColor("#3a3f4a")
_TEXT = QColor("#aab4c2")
_REFERENCE = QColor("#8a93a3")


def render_signature(
    signature: Signature,
    references: list[Signature] | None = None,
    *,
    width: int = 640,
    height: int = 260,
    title: str = "",
) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(_BACKGROUND)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont()
    font.setPixelSize(12)
    painter.setFont(font)

    plot = QRectF(48, 28, width - 64, height - 56)
    duration = max([signature.duration, *(r.duration for r in references or [])]) or 1.0
    top = max([signature.peak, *(r.peak for r in references or []), 0.01]) * 1.15

    def point(t: float, a: float) -> QPointF:
        return QPointF(
            plot.left() + plot.width() * t / duration,
            plot.bottom() - plot.height() * max(a, 0.0) / top,
        )

    painter.setPen(QPen(_GRID, 1))
    for i in range(5):
        y = plot.top() + plot.height() * i / 4
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(_TEXT)
        painter.drawText(
            QRectF(0, y - 8, plot.left() - 6, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{top * (4 - i) / 4:.2f}",
        )
        painter.setPen(QPen(_GRID, 1))
    painter.setPen(_TEXT)
    painter.drawText(QRectF(4, 6, width - 8, 16), Qt.AlignmentFlag.AlignLeft, title or "A")
    painter.drawText(
        QRectF(plot.left(), plot.bottom() + 6, plot.width(), 16),
        Qt.AlignmentFlag.AlignRight,
        f"{duration:.0f} s",
    )

    if references:
        steps = 120
        curve = QPolygonF(
            [
                point(duration * i / steps, reference_at(references, duration * i / steps))
                for i in range(steps + 1)
            ]
        )
        pen = QPen(_REFERENCE, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPolyline(curve)

    painter.setPen(QPen(QColor(palette().current), 2))
    painter.drawPolyline(QPolygonF([point(p.t, p.current) for p in signature.points]))
    painter.end()
    return image
