"""Power / DMM meter panel — a bench-instrument-style readout + trend graph.

Echoes the look of the bench PSU/DMM's own on-screen overlay: a DMM header (mode
+ big reading + unit), a cyan LCD-style 2x2 grid (voltage / current / power /
max current, with peak-hold), and a live trend graph of V/A/W below. Fed from a
:class:`TelemetrySnapshot` (DMM + PSU V/I); render-only, so it's testable
offscreen. Uses QtCharts (bundled — no new dependency). The monospace digits
approximate an LCD; a true 7-segment font can be bundled later.
"""

from __future__ import annotations

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from benchhud.instruments.power_history import PowerHistory
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.panel import Panel

_PLACEHOLDER = "—"

# Best-effort DMM unit from the OWON mode token (blank when unknown).
_DMM_UNITS = {
    "CONT": "Ω",
    "RES": "Ω",
    "OHM": "Ω",
    "VDC": "V",
    "VAC": "V",
    "ADC": "A",
    "AAC": "A",
    "DIODE": "V",
    "CAP": "F",
    "FREQ": "Hz",
    "TEMP": "°C",
}

_STYLE = """
QFrame#meterHeader {
    background: #f4fbfc; border: 1px solid #4f97a8; border-radius: 4px;
}
QFrame#meterCell {
    background: #86cddf; border: 1px solid #4f97a8; border-radius: 4px;
}
QLabel#meterCaption { color: #08343f; font-size: 8pt; }
QLabel#meterValue {
    color: #08343f; font-weight: bold; font-size: 15pt;
    font-family: "Menlo","Monaco","Consolas","DejaVu Sans Mono",monospace;
}
QLabel#meterMode { color: #3a6b78; font-size: 9pt; }
QLabel#meterPrimary {
    color: #08343f; font-weight: bold; font-size: 20pt;
    font-family: "Menlo","Monaco","Consolas","DejaVu Sans Mono",monospace;
}
QLabel#meterUnit { color: #3a6b78; font-size: 11pt; }
"""


class PowerPanel(Panel):
    panel_id = "power"
    panel_title = "Meter"

    def __init__(
        self,
        history: PowerHistory | None = None,
        window_seconds: float = 120.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history = history or PowerHistory()
        self._window = window_seconds
        self._max_current = 0.0
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(300)  # room for the LCD digits without clipping

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_grid())
        layout.addWidget(self._build_chart(), stretch=1)

    # --- header (DMM) ----------------------------------------------------

    def _build_header(self) -> QWidget:
        self._dmm_mode = QLabel("DMM")
        self._dmm_mode.setObjectName("meterMode")
        self._dmm_value = QLabel(_PLACEHOLDER)
        self._dmm_value.setObjectName("meterPrimary")
        self._dmm_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dmm_unit = QLabel("")
        self._dmm_unit.setObjectName("meterUnit")

        frame = QFrame()
        frame.setObjectName("meterHeader")
        frame.setMinimumHeight(52)
        row = QHBoxLayout(frame)
        row.addWidget(self._dmm_mode)
        row.addStretch(1)
        row.addWidget(self._dmm_value)
        row.addStretch(1)
        row.addWidget(self._dmm_unit)
        return frame

    # --- 2x2 grid (V / A / W / max) -------------------------------------

    def _build_grid(self) -> QWidget:
        self._v_value = _cell_value()
        self._a_value = _cell_value()
        self._w_value = _cell_value()
        self._max_value = _cell_value()

        self._reset_max_button = QPushButton("Reset max")
        self._reset_max_button.clicked.connect(self.reset_max)

        widget = QWidget()
        grid = QGridLayout(widget)
        grid.addWidget(_cell("VOLTAGE", self._v_value), 0, 0)
        grid.addWidget(_cell("CURRENT", self._a_value), 0, 1)
        grid.addWidget(_cell("POWER", self._w_value), 1, 0)
        grid.addWidget(_cell("MAX CURRENT", self._max_value, self._reset_max_button), 1, 1)
        return widget

    # --- trend graph -----------------------------------------------------

    def _build_chart(self) -> QWidget:
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
        return view

    # --- updates ---------------------------------------------------------

    def add_reading(self, voltage: float, current: float, t: float) -> None:
        """Record a PSU reading (power computed) and refresh the readouts + graph."""
        self._history.add(voltage, current, t)
        self._max_current = max(self._max_current, current)
        self._refresh()

    def update_from_snapshot(self, snapshot: TelemetrySnapshot, t: float) -> None:
        """Update the DMM header and, when PSU V+I are present, the power side."""
        self._update_dmm(snapshot)
        if snapshot.voltage is not None and snapshot.current is not None:
            self.add_reading(snapshot.voltage, snapshot.current, t)

    def reset_max(self) -> None:
        """Clear the peak-hold max current."""
        self._max_current = 0.0
        self._max_value.setText(f"{self._max_current:.2f} A")

    def _update_dmm(self, snapshot: TelemetrySnapshot) -> None:
        if not snapshot.ok:
            self._dmm_mode.setText("DMM")
            self._dmm_value.setText("ERR")
            self._dmm_unit.setText("")
            return
        dmm = snapshot.dmm
        if dmm is None:
            return
        self._dmm_mode.setText(dmm.mode or "DMM")
        if dmm.value is not None:
            self._dmm_value.setText(f"{dmm.value:g}")
        else:
            self._dmm_value.setText(dmm.status or _PLACEHOLDER)
        self._dmm_unit.setText(_DMM_UNITS.get(dmm.mode.upper(), ""))

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

        # Decimal places match the OWON display (V/A/max 2dp, W 1dp).
        latest = samples[-1]
        self._v_value.setText(f"{latest.voltage:.2f} V")
        self._a_value.setText(f"{latest.current:.2f} A")
        self._w_value.setText(f"{latest.power:.1f} W")
        self._max_value.setText(f"{self._max_current:.2f} A")

    # Read-only accessors for tests.
    @property
    def voltage_text(self) -> str:
        return self._v_value.text()

    @property
    def current_text(self) -> str:
        return self._a_value.text()

    @property
    def power_text(self) -> str:
        return self._w_value.text()

    @property
    def max_current_text(self) -> str:
        return self._max_value.text()

    @property
    def dmm_text(self) -> str:
        return self._dmm_value.text()

    @property
    def dmm_unit_text(self) -> str:
        return self._dmm_unit.text()


def _cell_value() -> QLabel:
    label = QLabel(_PLACEHOLDER)
    label.setObjectName("meterValue")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _cell(caption: str, value: QLabel, extra: QWidget | None = None) -> QFrame:
    frame = QFrame()
    frame.setObjectName("meterCell")
    frame.setMinimumHeight(68)
    box = QVBoxLayout(frame)
    box.setContentsMargins(8, 4, 8, 4)
    box.setSpacing(2)
    cap = QLabel(caption)
    cap.setObjectName("meterCaption")
    box.addWidget(cap)
    box.addWidget(value, stretch=1)
    if extra is not None:
        box.addWidget(extra)
    return frame
