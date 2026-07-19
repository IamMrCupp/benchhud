"""Tests for the dedicated power trend-graph panel (hardware-free)."""

from __future__ import annotations

from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.panels.power_graph import PowerGraphPanel


def test_metadata(qtbot) -> None:
    panel = PowerGraphPanel()
    qtbot.addWidget(panel)
    assert panel.panel_id == "power_graph"
    assert panel.objectName() == "panel__power_graph"


def test_starts_empty(qtbot) -> None:
    panel = PowerGraphPanel()
    qtbot.addWidget(panel)
    assert panel.v_series.count() == 0


def test_add_reading_appends_points(qtbot) -> None:
    panel = PowerGraphPanel()
    qtbot.addWidget(panel)
    panel.add_reading(12.0, 1.5, t=0.0)
    assert panel.v_series.count() == 1
    assert panel.a_series.count() == 1
    assert panel.w_series.count() == 1


def test_update_from_snapshot_with_psu(qtbot) -> None:
    panel = PowerGraphPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(voltage=5.0, current=2.0), t=1.0)
    assert panel.w_series.count() == 1


def test_update_from_snapshot_ignores_missing_psu(qtbot) -> None:
    panel = PowerGraphPanel()
    qtbot.addWidget(panel)
    panel.update_from_snapshot(TelemetrySnapshot(error="disconnected"), t=1.0)
    panel.update_from_snapshot(TelemetrySnapshot(voltage=5.0), t=2.0)  # no current
    assert panel.w_series.count() == 0


def test_rolling_window_trims_old_points(qtbot) -> None:
    panel = PowerGraphPanel(window_seconds=120.0)
    qtbot.addWidget(panel)
    panel.add_reading(1.0, 1.0, t=0.0)
    panel.add_reading(2.0, 1.0, t=200.0)  # 200s later -> first falls out of window
    assert panel.v_series.count() == 1
