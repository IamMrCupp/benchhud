"""Power trend graph — a dedicated V/A/W trend panel.

Split out of the meter so the graph gets real space (it lives in a full-width
bottom dock, like a scope trace). Backed by :class:`PowerHistory`; fed from the
same PSU readings as the meter. Render-only, testable offscreen. Uses QtCharts
(bundled — no new dependency).
"""

from __future__ import annotations

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QVBoxLayout, QWidget

from benchhud.instruments.power_history import PowerHistory
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.panel import Panel


class PowerGraphPanel(Panel):
    panel_id = "power_graph"
    panel_title = "Power Graph"

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
        self._axis_x.setTitleText("s")
        self._axis_y = QValueAxis()
        self._chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)
        for series in (self.v_series, self.a_series, self.w_series):
            self._chart.addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(self._axis_y)

        view = QChartView(self._chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)

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
        if not samples:
            return

        t_latest = samples[-1].t
        t_min = t_latest - self._window
        values: list[float] = []
        for s in samples:
            if s.t < t_min:
                continue
            self.v_series.append(s.t, s.voltage)
            self.a_series.append(s.t, s.current)
            self.w_series.append(s.t, s.power)
            values.extend((s.voltage, s.current, s.power))

        self._axis_x.setRange(t_min, t_latest if t_latest > t_min else t_min + 1)
        high = max(values) if values else 1.0
        low = min(0.0, min(values)) if values else 0.0
        self._axis_y.setRange(low, high * 1.1 if high > 0 else 1.0)
