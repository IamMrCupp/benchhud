"""Tests for the power panel (hardware-free)."""

from __future__ import annotations

from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.power import PowerPanel


def test_metadata(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    assert panel.panel_id == "power"
    assert panel.objectName() == "panel__power"


def test_starts_with_placeholders(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    assert "—" in panel.voltage_text
    assert panel.v_series.count() == 0


def test_add_reading_updates_readouts_and_series(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.add_reading(voltage=12.0, current=1.5, t=0.0)
    assert "12.000 V" in panel.voltage_text
    assert "1.500 A" in panel.current_text
    assert "18.00 W" in panel.power_text  # 12 * 1.5
    assert panel.v_series.count() == 1
    assert panel.w_series.count() == 1


def test_update_from_snapshot_with_psu(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(voltage=5.0, current=2.0), t=1.0)
    assert "10.00 W" in panel.power_text
    assert panel.w_series.count() == 1


def test_update_from_snapshot_ignores_missing_psu(qtbot) -> None:
    panel = PowerPanel()
    qtbot.addWidget(panel)
    # A DMM-only or error snapshot has no PSU V/I -> nothing recorded.
    panel.update_from_snapshot(TelemetrySnapshot(error="disconnected"), t=1.0)
    panel.update_from_snapshot(TelemetrySnapshot(voltage=5.0), t=2.0)  # current missing
    assert panel.w_series.count() == 0


def test_rolling_window_trims_old_points(qtbot) -> None:
    panel = PowerPanel(window_seconds=120.0)
    qtbot.addWidget(panel)
    panel.add_reading(1.0, 1.0, t=0.0)
    panel.add_reading(2.0, 1.0, t=200.0)  # 200s later -> first falls out of window
    assert panel.v_series.count() == 1  # only the recent point plotted


def test_shell_hosts_power_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert "power" in window.panels.panel_ids()
