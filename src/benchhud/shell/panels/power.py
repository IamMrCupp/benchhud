"""Power panel — meter-style V/A/W readouts + a live trend graph.

Mirrors what a bench PSU/DMM shows on its own display: the current voltage,
current, and wattage as big numbers, plus a scrolling graph of all three so you
can watch the trend (e.g. a current spike when a shorted board is powered). Fed
from :class:`PowerHistory` + the OWON PSU readings; render-only, so it's testable
offscreen with synthesized readings. Uses QtCharts (bundled — no new dependency).
"""

from __future__ import annotations

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from benchhud.instruments.power_history import PowerHistory
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.panel import Panel

_PLACEHOLDER = "—"


class PowerPanel(Panel):
    panel_id = "power"
    panel_title = "Power"

    def __init__(
        self,
        history: PowerHistory | None = None,
        window_seconds: float = 120.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history = history or PowerHistory()
        self._window = window_seconds

        self._v_label = _readout(f"{_PLACEHOLDER} V")
        self._a_label = _readout(f"{_PLACEHOLDER} A")
        self._w_label = _readout(f"{_PLACEHOLDER} W")
        readouts = QHBoxLayout()
        for label in (self._v_label, self._a_label, self._w_label):
            readouts.addWidget(label)

        self.v_series = QLineSeries()
        self.v_series.setName("V")
        self.a_series = QLineSeries()
        self.a_series.setName("A")
        self.w_series = QLineSeries()
        self.w_series.setName("W")

        self._chart = QChart()
        self._axis_x = QValueAxis()
        self._axis_x.setTitleText("s")
        self._axis_y = QValueAxis()
        self._chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)
        for series in (self.v_series, self.a_series, self.w_series):
            self._chart.addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(self._axis_y)

        self._view = QChartView(self._chart)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)

        layout = QVBoxLayout(self)
        layout.addLayout(readouts)
        layout.addWidget(self._view, stretch=1)

    def add_reading(self, voltage: float, current: float, t: float) -> None:
        """Record a PSU reading (power computed) and refresh the view."""
        self._history.add(voltage, current, t)
        self._refresh()

    def update_from_snapshot(self, snapshot: TelemetrySnapshot, t: float) -> None:
        """Record from a telemetry snapshot when it carries PSU V + I."""
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

        latest = samples[-1]
        self._v_label.setText(f"{latest.voltage:.3f} V")
        self._a_label.setText(f"{latest.current:.3f} A")
        self._w_label.setText(f"{latest.power:.2f} W")

    # Read-only accessors for tests.
    @property
    def voltage_text(self) -> str:
        return self._v_label.text()

    @property
    def current_text(self) -> str:
        return self._a_label.text()

    @property
    def power_text(self) -> str:
        return self._w_label.text()


def _readout(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = label.font()
    font.setPointSize(18)
    font.setBold(True)
    label.setFont(font)
    return label
