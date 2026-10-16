"""A readable, steady hot-spot marker (#271)."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.overlay.composite import (
    MarkerTracker,
    OverlayMode,
    OverlaySettings,
    compose,
    draw_marker,
)
from benchhud.overlay.transform import RegistrationTransform


def frame(spots: dict[tuple[int, int], float], ambient: float = 25.0) -> np.ndarray:
    temps = np.full((192, 256), ambient, dtype=np.float32)
    for (x, y), value in spots.items():
        temps[y, x] = value
    return temps


def test_it_holds_its_spot_against_a_near_tie() -> None:
    tracker = MarkerTracker(smoothing=1.0)
    assert tracker.update(frame({(50, 50): 40.0, (200, 150): 39.9}))[:2] == (50, 50)
    # Noise flips which is hottest by a hair: the marker stays put.
    for _ in range(10):
        x, y, _v = tracker.update(frame({(50, 50): 40.0, (200, 150): 40.3}))
        assert (x, y) == (50, 50)


def test_it_moves_when_another_spot_is_clearly_hotter() -> None:
    tracker = MarkerTracker(smoothing=1.0)
    tracker.update(frame({(50, 50): 40.0}))
    assert tracker.update(frame({(50, 50): 40.0, (200, 150): 41.0}))[:2] == (200, 150)


def test_it_follows_a_spot_that_cools_away() -> None:
    tracker = MarkerTracker(smoothing=1.0)
    tracker.update(frame({(50, 50): 60.0}))
    assert tracker.update(frame({(50, 50): 26.0, (120, 90): 45.0}))[:2] == (120, 90)


def test_it_glides_rather_than_jumps() -> None:
    tracker = MarkerTracker(smoothing=0.5)
    tracker.update(frame({(0, 0): 40.0}))
    x, _y, _v = tracker.update(frame({(0, 0): 30.0, (100, 0): 50.0}))
    assert x == pytest.approx(50.0)  # halfway there on the first frame
    for _ in range(20):
        x, _y, _v = tracker.update(frame({(0, 0): 30.0, (100, 0): 50.0}))
    assert x == pytest.approx(100.0, abs=0.01)


def test_the_marker_scales_with_the_frame() -> None:
    def footprint(width: int) -> int:
        blank = np.zeros((width * 9 // 16, width, 3), dtype=np.uint8)
        out = draw_marker(blank, width // 2, width * 9 // 32, "40.0C")
        return int((out.any(axis=2)).sum())

    assert footprint(1920) > 6 * footprint(640)  # area grows with the frame


def test_it_reads_on_a_bright_board() -> None:
    yellow = np.full((360, 640, 3), (0, 255, 255), dtype=np.uint8)  # BGR yellow
    out = draw_marker(yellow, 320, 180, "40.0C")
    assert (out.sum(axis=2) < 60).any()  # the dark edge is there to see


def test_compose_maps_the_tracked_spot_through_the_registration() -> None:
    scope = np.zeros((720, 1280, 3), dtype=np.uint8)
    temps = frame({(128, 96): 40.0})
    transform = RegistrationTransform(offset_x=100.0)
    out = compose(
        scope, temps, transform, OverlaySettings(mode=OverlayMode.MARKER), (128, 96, 40.0)
    )
    cx, cy = transform.map_point(128, 96, (256, 192), (1280, 720))
    ys, xs = np.nonzero(out.any(axis=2))
    assert abs(np.median(xs) - cx) < 40 and abs(np.median(ys) - cy) < 40


def noisy_board(seed: int) -> np.ndarray:
    """A board at room temperature: a degree of sensor noise, nothing hot."""
    rng = np.random.default_rng(seed)
    return (30.0 + rng.normal(0.0, 0.5, (192, 256))).astype(np.float32)


def test_no_marker_when_nothing_stands_out() -> None:
    """On a flat board the hottest pixel is noise; marking it made the marker hop (#278)."""
    tracker = MarkerTracker()
    for seed in range(5):
        assert tracker.update(noisy_board(seed)) is None
    temps = noisy_board(9)
    temps[100, 120] = 45.0  # the iron touches down
    assert tracker.update(temps)[:2] == (120, 100)


def test_compose_marks_nothing_on_a_flat_board() -> None:
    scope = np.full((192, 256, 3), 80, dtype=np.uint8)
    settings = OverlaySettings(mode=OverlayMode.MARKER, scale_bar=False)
    out = compose(scope, noisy_board(1), RegistrationTransform(), settings)
    assert np.array_equal(out, scope)
