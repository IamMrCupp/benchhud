"""Live V/A/W trend chart.

A plain widget (not a dock) embedded in the meter panel beneath the numeric
readouts, so the instrument reads as one thing. Backed by :class:`PowerHistory`
and fed from the same PSU readings as the numerics. Render-only and testable
offscreen. Uses QtCharts (bundled — no new dependency).
"""

from __future__ import annotations

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from benchhud.instruments.power_history import PowerHistory
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.palette import palette


class PowerChart(QWidget):
    """Scrolling V/A/W trend over a rolling time window."""

    def __init__(
        self,
        history: PowerHistory | None = None,
        window_seconds: float = 120.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history = history or PowerHistory()
        self._window = window_seconds

        self.v_series = QLineSeries()
        self.v_series.setName("V")
        self.a_series = QLineSeries()
        self.a_series.setName("A")
        self.w_series = QLineSeries()
        self.w_series.setName("W")

        self._chart = QChart()
        self._chart.setTheme(QChart.ChartTheme.ChartThemeDark)
        self._axis_x = QValueAxis()
        self._axis_x.setLabelFormat("%.0f s")
        self._axis_x.setTickCount(3)
        self._axis_y = QValueAxis()  # volts and watts, on the left
        self._axis_y.setLabelFormat("%.1f")
        # Amps get their own axis on the right: on one shared axis a 0.4 A curve
        # lay flat under 12 V, and so did the known-good curve (#236).
        self._axis_a = QValueAxis()
        self._axis_a.setLabelFormat("%.2f")  # in the current colour: no unit needed
        self._chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)
        self._chart.addAxis(self._axis_a, Qt.AlignmentFlag.AlignRight)
        colours = palette()
        for series, colour, axis in (
            (self.v_series, colours.voltage, self._axis_y),
            (self.a_series, colours.current, self._axis_a),
            (self.w_series, colours.power, self._axis_y),
        ):
            self._chart.addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(axis)
            # After addSeries: the chart theme recolours series as they're added.
            series.setPen(QPen(QColor(colour), 2))
        self._axis_a.setLabelsColor(QColor(colours.current))

        # The known-good power-on curve (#236), dashed behind the live current
        # and anchored to when the output came on. Empty until there is one.
        self.reference_series = QLineSeries()
        self.reference_series.setName("good")
        self._chart.addSeries(self.reference_series)
        self.reference_series.attachAxis(self._axis_x)
        self.reference_series.attachAxis(self._axis_a)
        self.reference_series.setPen(QPen(QColor("#8a93a3"), 2, Qt.PenStyle.DashLine))
        self._reference: list[tuple[float, float]] = []

        view = QChartView(self._chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)

    @property
    def history(self) -> PowerHistory:
        """The samples behind the chart (the on-stream graph overlay reads these too)."""
        return self._history

    def set_reference(self, points: list[tuple[float, float]], t0: float) -> None:
        """Show a known-good curve of (seconds after power-on, amps) from ``t0``."""
        self._reference = [(t0 + t, a) for t, a in points]
        self._refresh()

    def clear_reference(self) -> None:
        self._reference = []
        self._refresh()

    def add_reading(self, voltage: float, current: float, t: float) -> None:
        self._history.add(voltage, current, t)
        self._refresh()

    def update_from_snapshot(self, snapshot: TelemetrySnapshot, t: float) -> None:
        if snapshot.voltage is not None and snapshot.current is not None:
            self.add_reading(snapshot.voltage, snapshot.current, t)

    def _refresh(self) -> None:
        samples = self._history.samples()
        self.v_series.clear()
        self.a_series.clear()
        self.w_series.clear()
        self.reference_series.clear()
        if not samples:
            return

        t_latest = samples[-1].t
        # Don't show empty time before the first sample (early on there's less
        # data than the window is wide).
        t_min = max(t_latest - self._window, samples[0].t)
        values: list[float] = []
        amps: list[float] = []
        for s in samples:
            if s.t < t_min:
                continue
            self.v_series.append(s.t, s.voltage)
            self.a_series.append(s.t, s.current)
            self.w_series.append(s.t, s.power)
            values.extend((s.voltage, s.power))
            amps.append(s.current)
        for t, current in self._reference:
            if t_min <= t <= t_latest:
                self.reference_series.append(t, current)
                amps.append(current)

        self._axis_x.setRange(t_min, t_latest if t_latest > t_min else t_min + 1)
        high = max(values) if values else 1.0
        low = min(0.0, min(values)) if values else 0.0
        self._axis_y.setRange(low, high * 1.1 if high > 0 else 1.0)
        top_a = max(amps) if amps else 0.0
        self._axis_a.setRange(min(0.0, min(amps, default=0.0)), top_a * 1.1 if top_a > 0 else 0.1)
