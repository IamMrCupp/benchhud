"""The known-good power-on curve on the stream's graph (#263)."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.browser_sources.payloads import graph_payload
from benchhud.config import Config, GraphOverlayConfig
from benchhud.instruments.power_history import PowerSample
from benchhud.overlay.graph import build_graph_model, draw_graph_overlay

WINDOW = 10.0


def samples(current) -> list[PowerSample]:
    return [
        PowerSample(t=100.0 + i * 0.5, voltage=12.0, current=current(i * 0.5)) for i in range(21)
    ]


def stalled(_t):
    return 0.05


def good_curve(t0: float = 100.0) -> list[tuple[float, float]]:
    return [(t0 + i * 0.5, 0.05 if i < 4 else 0.40) for i in range(21)]


def test_the_good_curve_shares_the_current_scale() -> None:
    model = build_graph_model(samples(stalled), WINDOW, good_curve())
    current = [y for _, y in model.lines[1]]
    reference = [y for _, y in model.reference]
    assert max(reference) == pytest.approx(1.0)  # 0.40 A is the top of the shared scale
    assert max(current) == pytest.approx(0.05 / 0.40)  # the stall sits low against it
    assert model.reference[0][0] == pytest.approx(0.0) and model.reference[-1][0] == pytest.approx(
        1.0
    )


def test_without_a_curve_nothing_changes() -> None:
    plain = build_graph_model(samples(stalled), WINDOW)
    assert plain.reference == ()
    # Scaled against itself, a flat 50 mA stall draws at the top and looks
    # healthy. That's why the good curve has to share its scale.
    assert plain.lines[1][-1][1] == 1.0


def test_points_outside_the_window_are_dropped() -> None:
    old = [(t - 60.0, a) for t, a in good_curve()]  # a recording a minute ago
    assert build_graph_model(samples(stalled), WINDOW, old).reference == ()


def test_the_overlay_draws_it(qapp) -> None:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    config = GraphOverlayConfig(enabled=True)
    with_ref = draw_graph_overlay(frame, samples(stalled), config, reference=good_curve())
    without = draw_graph_overlay(frame, samples(stalled), config)
    assert not np.array_equal(with_ref, without)


def test_the_browser_source_gets_it() -> None:
    body = graph_payload(samples(stalled), WINDOW, reference=good_curve())
    assert len(body["reference"]) == 21
    assert graph_payload(samples(stalled), WINDOW)["reference"] == []


def test_the_window_feeds_the_charts_curve_to_the_stream(qtbot) -> None:
    from benchhud.jobs.store import JobStore
    from benchhud.shell.main_window import MainWindow

    window = MainWindow(Config(), job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    window.power_panel.chart.set_reference([(0.0, 0.1), (5.0, 0.4)], 100.0)
    assert window.power_panel.chart.reference == [(100.0, 0.1), (105.0, 0.4)]
