"""On-stream power graph: model (pure), rendering, stacking beside the meter."""

from __future__ import annotations

import numpy as np

from benchhud.config import Config, GraphOverlayConfig, TelemetryOverlayConfig
from benchhud.instruments.power_history import PowerSample
from benchhud.overlay.graph import build_graph_model, draw_graph_overlay, render_graph_image
from benchhud.overlay.telemetry import blit_rgba, draw_telemetry_overlay_sized


def _samples(n: int = 20, dt: float = 1.0) -> list[PowerSample]:
    return [PowerSample(t=i * dt, voltage=5.0, current=0.1 + 0.01 * i) for i in range(n)]


def _frame(h: int = 720, w: int = 1280) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_model_is_empty_without_enough_history():
    assert build_graph_model([], 60).is_empty
    assert build_graph_model(_samples(1), 60).is_empty


def test_latest_values_feed_the_legend():
    model = build_graph_model(_samples(5), 60)
    v, a, w = model.latest
    assert (v, round(a, 3)) == (5.0, 0.14)
    assert round(w, 3) == 0.7


def test_each_series_is_scaled_to_its_own_range():
    """A small current trace must not be flattened by the voltage (per-series ranges)."""
    model = build_graph_model(_samples(20), 60)
    _v_line, a_line, _w_line = model.lines
    ys = [y for _x, y in a_line]
    assert max(ys) == 1.0  # the current fills its own panel height
    assert min(ys) > 0.3  # anchored at zero: 0.10 A of 0.29 A peak


def test_flat_series_draws_along_the_floor():
    flat = [PowerSample(t=float(i), voltage=0.0, current=0.0) for i in range(5)]
    model = build_graph_model(flat, 60)
    assert all(y == 0.0 for _x, y in model.lines[0])


def test_only_the_recent_window_is_drawn_and_now_is_the_right_edge():
    model = build_graph_model(_samples(100), 30)
    xs = [x for x, _y in model.lines[0]]
    assert len(xs) == 31  # t = 69..99 inclusive
    assert xs[-1] == 1.0
    assert xs[0] >= 0.0


def test_short_history_starts_partway_rather_than_stretching():
    model = build_graph_model(_samples(10), 60)
    xs = [x for x, _y in model.lines[0]]
    assert xs[0] > 0.8  # 9 s of history in a 60 s window


def test_render_produces_a_panel(qtbot):
    image = render_graph_image(build_graph_model(_samples(), 60))
    assert not image.isNull()
    assert image.width() > image.height() > 0


def test_draw_changes_the_frame_in_the_configured_corner(qtbot):
    frame = _frame()
    out = draw_graph_overlay(frame, _samples(), GraphOverlayConfig(corner="bottom-right"))
    assert out is not frame
    changed = np.argwhere(out.any(axis=2))
    assert changed[:, 1].min() > frame.shape[1] // 2  # right half
    assert changed[:, 0].min() > frame.shape[0] // 2  # bottom half


def test_enabled_graph_with_no_readings_still_draws_its_panel(qtbot):
    """#187: no PSU on the bench must not make an enabled overlay invisible."""
    frame = _frame()
    out = draw_graph_overlay(frame, [], GraphOverlayConfig(corner="bottom-right"))
    assert out is not frame
    assert out.any()


def test_empty_model_has_no_values_yet():
    model = build_graph_model([], 60)
    assert model.is_empty
    assert model.latest is None


def test_placeholder_matches_the_meter_overlay():
    from benchhud.overlay.graph import _format

    assert _format(None, "V") == "—"
    assert _format(5.0, "V") == "5.00 V"
    assert _format(0.1234, "A") == "0.123 A"


def test_empty_panel_renders(qtbot):
    image = render_graph_image(build_graph_model([], 60))
    assert not image.isNull()
    assert image.width() > image.height() > 0


def test_stack_moves_an_overlay_off_its_corner_edge():
    frame = _frame(200, 200)
    patch = np.full((20, 20, 4), 255, dtype=np.uint8)
    base = blit_rgba(frame, patch, "bottom-left", 0)
    stacked = blit_rgba(frame, patch, "bottom-left", 0, stack=50)
    assert base[199, 0].any() and not stacked[199, 0].any()
    assert stacked[149, 0].any()
    top = blit_rgba(frame, patch, "top-left", 0, stack=50)
    assert top[50, 0].any() and not top[0, 0].any()


def test_window_stacks_the_graph_beside_the_meter_in_a_shared_corner(qtbot):
    """Both overlays in one corner: the graph sits above the meter, never on it."""
    from benchhud.shell.main_window import MainWindow

    config = Config()
    config.telemetry_overlay = TelemetryOverlayConfig(enabled=True, corner="bottom-left")
    config.graph_overlay = GraphOverlayConfig(enabled=True, corner="bottom-left", opacity=1.0)
    window = MainWindow(config)
    qtbot.addWidget(window)
    for s in _samples():
        window.power_panel.chart.add_reading(s.voltage, s.current, s.t)

    only_meter = Config()
    only_meter.telemetry_overlay = config.telemetry_overlay
    meter_window = MainWindow(only_meter)
    qtbot.addWidget(meter_window)

    both = window._apply_overlays(_frame())
    meter = meter_window._apply_overlays(_frame())
    meter_top = np.argwhere(meter.any(axis=2))[:, 0].min()
    both_top = np.argwhere(both.any(axis=2))[:, 0].min()
    assert both_top < meter_top  # the graph added rows above the meter
    # The meter's own pixels are untouched by the graph.
    assert np.array_equal(both[meter_top:], meter[meter_top:])


def test_a_fixed_width_sets_the_panel_width():
    model = build_graph_model(_samples(), 60)
    assert render_graph_image(model, width=272).width() == 272
    assert render_graph_image(model, width=500).width() == 500
    assert render_graph_image(model).width() == 336  # unstacked: its own natural size


def test_the_legend_shrinks_to_fit_a_narrow_panel():
    """Shrunk to a small meter's width, the legend's text shrinks rather than overflowing."""
    from PySide6.QtGui import QFontMetrics

    from benchhud.overlay.graph import _fit_legend_font, _legend_texts

    texts = _legend_texts((12.34, 0.567, 6.99))
    roomy = _fit_legend_font(texts, 13, 16, 400)
    tight = _fit_legend_font(texts, 13, 16, 200)
    assert roomy.pixelSize() == 13  # untouched when it fits
    assert tight.pixelSize() < 13
    metrics = QFontMetrics(tight)
    assert sum(metrics.horizontalAdvance(t) for t in texts) + 2 * 16 <= 200


def _columns(frame: np.ndarray, rows: slice) -> tuple[int, int]:
    """First and last painted column within ``rows``."""
    cols = np.argwhere(frame[rows].any(axis=2))[:, 1]
    return int(cols.min()), int(cols.max())


def test_stacked_graph_lines_up_with_the_meter(qtbot):
    """#196: in the same corner, the graph matches the meter's width and inset,
    even when the two are sized differently in Preferences."""
    from benchhud.shell.main_window import MainWindow

    for corner in ("top-right", "bottom-left"):
        for graph_scale in (1.0, 1.4, 0.6):
            config = Config()
            config.telemetry_overlay = TelemetryOverlayConfig(enabled=True, corner=corner)
            config.graph_overlay = GraphOverlayConfig(
                enabled=True, corner=corner, opacity=1.0, scale=graph_scale
            )
            window = MainWindow(config)
            qtbot.addWidget(window)
            for s in _samples():
                window.power_panel.chart.add_reading(s.voltage, s.current, s.t)

            _, footprint = draw_telemetry_overlay_sized(
                _frame(), window._meter_readout(), config.telemetry_overlay
            )
            out = window._apply_overlays(_frame())
            h = out.shape[0]
            if corner.startswith("top"):
                meter_rows = slice(0, footprint.height)
                graph_rows = slice(footprint.height, h)
            else:
                meter_rows = slice(h - footprint.height, h)
                graph_rows = slice(0, h - footprint.height)
            assert _columns(out, graph_rows) == _columns(out, meter_rows), (corner, graph_scale)


def test_graph_overlay_config_round_trips():
    config = Config.from_dict(
        {"graph_overlay": {"enabled": True, "corner": "top-right", "window_seconds": 30.0}}
    )
    assert config.graph_overlay.enabled
    assert config.graph_overlay.corner == "top-right"
    assert config.graph_overlay.window_seconds == 30.0
    assert not Config().graph_overlay.enabled  # off by default
