"""Tests for the embedded power trend chart (hardware-free)."""

from __future__ import annotations

from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.power_chart import PowerChart


def test_starts_empty(qtbot) -> None:
    chart = PowerChart()
    qtbot.addWidget(chart)
    assert chart.v_series.count() == 0


def test_add_reading_appends_points(qtbot) -> None:
    chart = PowerChart()
    qtbot.addWidget(chart)
    chart.add_reading(12.0, 1.5, t=0.0)
    assert chart.v_series.count() == 1
    assert chart.a_series.count() == 1
    assert chart.w_series.count() == 1


def test_update_from_snapshot_with_psu(qtbot) -> None:
    chart = PowerChart()
    qtbot.addWidget(chart)
    chart.update_from_snapshot(TelemetrySnapshot(voltage=5.0, current=2.0), t=1.0)
    assert chart.w_series.count() == 1


def test_update_from_snapshot_ignores_missing_psu(qtbot) -> None:
    chart = PowerChart()
    qtbot.addWidget(chart)
    chart.update_from_snapshot(TelemetrySnapshot(error="disconnected"), t=1.0)
    chart.update_from_snapshot(TelemetrySnapshot(voltage=5.0), t=2.0)  # no current
    assert chart.w_series.count() == 0


def test_rolling_window_trims_old_points(qtbot) -> None:
    chart = PowerChart(window_seconds=120.0)
    qtbot.addWidget(chart)
    chart.add_reading(1.0, 1.0, t=0.0)
    chart.add_reading(2.0, 1.0, t=200.0)  # 200s later -> first falls out of window
    assert chart.v_series.count() == 1


def test_axis_does_not_show_time_before_the_first_sample(qtbot) -> None:
    chart = PowerChart(window_seconds=120.0)
    qtbot.addWidget(chart)
    chart.add_reading(12.0, 1.0, t=0.0)
    chart.add_reading(12.0, 1.0, t=30.0)  # only 30s of data in a 120s window
    assert chart._axis_x.min() >= 0.0  # not -90
