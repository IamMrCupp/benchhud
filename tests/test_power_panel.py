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
    assert "12.00 V" in panel.voltage_text
    assert "1.50 A" in panel.current_text
    assert "18.0 W" in panel.power_text  # 12 * 1.5


def test_update_from_snapshot_with_psu(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(voltage=5.0, current=2.0))
    assert "10.0 W" in panel.power_text


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
    assert "1.90 A" in panel.max_current_text
    panel.reset_max()
    assert "0.00 A" in panel.max_current_text


def test_shell_hosts_power_and_graph_panels(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    ids = window.panels.panel_ids()
    assert "power" in ids
    assert "power_graph" in ids
