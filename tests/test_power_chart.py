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


def test_a_refresh_sets_each_series_once_not_point_by_point(qtbot, monkeypatch) -> None:
    """A full window re-added point by point took a third of the GUI thread (#350)."""
    chart = PowerChart()
    qtbot.addWidget(chart)
    for i in range(50):
        chart.add_reading(12.0, 0.5, float(i))
    chart.set_reference([(0.0, 0.4), (1.0, 0.6)], t0=10.0)

    calls: dict[str, int] = {}
    for name in ("v_series", "a_series", "w_series", "reference_series"):
        series = getattr(chart, name)

        def no_append(*_args, _name=name) -> None:
            raise AssertionError(f"{_name}.append called during a refresh")

        def counting_replace(points, _series=series, _name=name) -> None:
            calls[_name] = calls.get(_name, 0) + 1
            type(_series).replace(_series, points)

        monkeypatch.setattr(series, "append", no_append)
        monkeypatch.setattr(series, "replace", counting_replace)

    chart.add_reading(12.0, 0.5, 50.0)

    assert calls == {"v_series": 1, "a_series": 1, "w_series": 1, "reference_series": 1}
    assert chart.v_series.count() == 51
    assert chart.reference_series.count() == 2


def test_the_reference_curve_is_trimmed_to_the_window(qtbot) -> None:
    chart = PowerChart(window_seconds=10.0)
    qtbot.addWidget(chart)
    for i in range(30):
        chart.add_reading(5.0, 0.2, float(i))
    chart.set_reference([(0.0, 0.1), (25.0, 0.3), (40.0, 0.9)], t0=0.0)
    assert [(p.x(), p.y()) for p in chart.reference_series.points()] == [(25.0, 0.3)]
