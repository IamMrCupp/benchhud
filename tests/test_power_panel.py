"""Tests for the numeric power/DMM meter panel (hardware-free)."""

from __future__ import annotations

from benchhud.instruments.owon_spm8104 import DmmReading
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.power import PowerPanel


def _dmm(mode, value=None, status=None) -> DmmReading:
    return DmmReading(mode=mode, value=value, status=status, ranging="Auto", raw="")


def test_metadata(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    assert panel.panel_id == "power"
    assert panel.objectName() == "panel__power"


def test_starts_with_placeholders(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    assert "—" in panel.voltage_text


def test_add_reading_updates_readouts(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.add_reading(voltage=12.0, current=1.5)
    # Digits only — the unit is in the cell caption, not the LCD value.
    assert panel.voltage_text == "12.00"
    assert panel.current_text == "1.50"
    assert panel.power_text == "18.0"  # 12 * 1.5


def test_update_from_snapshot_with_psu(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(voltage=5.0, current=2.0))
    assert panel.power_text == "10.0"


def test_update_from_snapshot_ignores_missing_psu(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(error="disconnected"))
    panel.update_from_snapshot(TelemetrySnapshot(voltage=5.0))  # current missing
    assert "—" in panel.voltage_text  # numbers never updated


def test_dmm_header_shows_numeric_reading_with_unit(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(dmm=_dmm("VDC", value=20.14)))
    assert "20.14" in panel.dmm_text
    assert panel.dmm_unit_text == "V"


def test_dmm_header_shows_status_token(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(dmm=_dmm("CONT", status="OL")))
    assert "OL" in panel.dmm_text
    assert panel.dmm_unit_text == "Ω"


def test_dmm_header_error(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(error="disconnected"))
    assert panel.dmm_text == "ERR"


def test_max_current_peak_hold_and_reset(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.add_reading(4.2, 0.5)
    panel.add_reading(4.2, 1.9)  # peak
    panel.add_reading(4.2, 0.3)  # falls back, but max holds
    assert panel.max_current_text == "1.90"
    panel.reset_max()
    assert panel.max_current_text == "0.00"


def test_shell_hosts_power_panel_with_embedded_chart(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert "power" in window.panels.panel_ids()
    # the trend chart is embedded in the meter, not a separate dock
    assert "power_graph" not in window.panels.panel_ids()


def test_meter_embeds_the_trend_chart(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    assert panel.chart.v_series.count() == 0
    panel.update_from_snapshot(TelemetrySnapshot(voltage=12.0, current=1.5), t=0.0)
    # one entry point updates both the numerics and the embedded chart
    assert panel.voltage_text == "12.00"
    assert panel.chart.v_series.count() == 1
    assert panel.chart.w_series.count() == 1


def test_meter_cells_fit_their_widest_reading(qtbot):
    """Digits were clipping at narrow dock widths — the cell must own the space."""
    from PySide6.QtGui import QFont, QFontMetrics

    from benchhud.shell.panels.power import cell_min_width

    panel = PowerPanel()
    qtbot.addWidget(panel)
    # Measured in a plain proportional face; the real cell uses the wider LCD
    # font, so the cell must be at least this wide for the digits-only value.
    font = QFont()
    font.setPointSize(16)
    font.setBold(True)
    needed = QFontMetrics(font).horizontalAdvance("-000.00")
    assert cell_min_width() > needed
    assert panel.minimumSizeHint().width() >= 2 * cell_min_width()
