"""Power / DMM meter panel — a bench-instrument-style numeric readout.

Echoes the bench PSU/DMM's own on-screen overlay: a DMM header (mode + big
reading + unit) and a cyan LCD-style 2x2 grid (voltage / current / power / max
current, with peak-hold) with the live V/A/W trend chart beneath it, so the
whole thing reads as one instrument. Fed from a :class:`TelemetrySnapshot`;
render-only, testable offscreen. Monospace digits approximate an LCD; a true
7-segment font can be bundled later.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.panel import Panel
from benchhud.shell.power_chart import PowerChart

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
/* Labels must be transparent so the cyan cell shows through — otherwise the
   global dark theme paints their background dark and the digits vanish. */
QLabel#meterCaption { color: #0a3a45; background: transparent; font-size: 8pt; }
QLabel#meterValue {
    color: #062028; background: transparent; font-weight: bold; font-size: 16pt;
    font-family: "Menlo","Monaco","Consolas","DejaVu Sans Mono",monospace;
}
QLabel#meterMode { color: #2f6472; background: transparent; font-size: 9pt; }
QLabel#meterPrimary {
    color: #062028; background: transparent; font-weight: bold; font-size: 18pt;
    font-family: "Menlo","Monaco","Consolas","DejaVu Sans Mono",monospace;
}
QLabel#meterUnit { color: #2f6472; background: transparent; font-size: 11pt; }
"""


class PowerPanel(Panel):
    panel_id = "power"
    panel_title = "Meter"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_current = 0.0
        self._last_snapshot: TelemetrySnapshot | None = None
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(220)

        self.chart = PowerChart()
        self.chart.setMinimumHeight(150)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_header())
        layout.addWidget(self._build_grid())
        layout.addWidget(self.chart, stretch=1)

    def _build_header(self) -> QWidget:
        self._dmm_mode = QLabel("DMM")
        self._dmm_mode.setObjectName("meterMode")
        self._dmm_value = QLabel(_PLACEHOLDER)
        self._dmm_value.setObjectName("meterPrimary")
        self._dmm_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dmm_value.setMinimumHeight(34)
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

    def add_reading(self, voltage: float, current: float, t: float = 0.0) -> None:
        """Update the numeric readouts and append the reading to the trend chart."""
        self._max_current = max(self._max_current, current)
        self.chart.add_reading(voltage, current, t)
        # Decimal places match the OWON display (V/A/max 2dp, W 1dp).
        self._v_value.setText(f"{voltage:.2f} V")
        self._a_value.setText(f"{current:.2f} A")
        self._w_value.setText(f"{voltage * current:.1f} W")
        self._max_value.setText(f"{self._max_current:.2f} A")

    def update_from_snapshot(self, snapshot: TelemetrySnapshot, t: float = 0.0) -> None:
        """Update the DMM header and, when PSU V+I are present, the numeric grid."""
        self._last_snapshot = snapshot
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

    @property
    def last_snapshot(self) -> TelemetrySnapshot | None:
        """The most recent telemetry snapshot (for capture-to-job)."""
        return self._last_snapshot

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
    label.setMinimumHeight(28)  # guarantee vertical room for the LCD digits
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
