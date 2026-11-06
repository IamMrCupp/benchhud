"""The on-air sensitivity model, and the guarantee that gives it a point.

The headline test is :func:`test_a_sensitive_layer_never_reaches_the_sink`: a
tagged-sensitive surface is shown on the operator's local preview but its bytes
never reach the output sink. That property is the whole reason the recon panels
are allowed to composite at all, so it gets an end-to-end test through the real
``MainWindow`` composite path, not just the pure helpers.
"""

from __future__ import annotations

import numpy as np

from benchhud.config import Config
from benchhud.output.onair import Draw, Layer, compose_on_air, compose_preview, excluded
from benchhud.output.sink import FakeSink
from benchhud.shell.main_window import MainWindow

SAFE = (5, 130, 240)  # a non-sensitive layer paints the left stripe
SECRET = (17, 213, 99)  # a sensitive layer paints the right stripe


def _stripe(colour: tuple[int, int, int], *, left: bool) -> Draw:
    """A draw that paints a 10-px stripe so each layer's pixels are findable."""

    def draw(frame: np.ndarray) -> np.ndarray:
        if left:
            frame[:, :10] = colour
        else:
            frame[:, -10:] = colour
        return frame

    return draw


def _has(frame: np.ndarray, colour: tuple[int, int, int]) -> bool:
    return bool(np.any(np.all(frame == colour, axis=-1)))


# --- the pure helpers -------------------------------------------------------


def test_compose_on_air_drops_sensitive_layers() -> None:
    base = np.zeros((20, 40, 3), dtype=np.uint8)
    layers = [
        Layer("safe", _stripe(SAFE, left=True)),
        Layer("secret", _stripe(SECRET, left=False), sensitive=True),
    ]

    out = compose_on_air(base.copy(), layers)

    assert _has(out, SAFE)
    assert not _has(out, SECRET)


def test_compose_preview_draws_everything() -> None:
    base = np.zeros((20, 40, 3), dtype=np.uint8)
    layers = [
        Layer("safe", _stripe(SAFE, left=True)),
        Layer("secret", _stripe(SECRET, left=False), sensitive=True),
    ]

    out = compose_preview(base.copy(), layers)

    assert _has(out, SAFE)
    assert _has(out, SECRET)


def test_excluded_names_only_the_sensitive_layers() -> None:
    layers = [
        Layer("safe", _stripe(SAFE, left=True)),
        Layer("secret", _stripe(SECRET, left=False), sensitive=True),
    ]

    assert excluded(layers) == ("secret",)


def test_no_layers_is_the_base_unchanged() -> None:
    base = np.full((8, 8, 3), 7, dtype=np.uint8)

    assert np.array_equal(compose_on_air(base), base)
    assert np.array_equal(compose_preview(base), base)


# --- the end-to-end guarantee ----------------------------------------------


def _enabled_config(width: int = 64, height: int = 48) -> Config:
    config = Config()
    config.output.enabled = True
    config.output.device = "/dev/video10"
    config.output.width = width
    config.output.height = height
    return config


def test_a_sensitive_layer_never_reaches_the_sink(qtbot) -> None:
    """A tagged surface is on the preview, absent from the virtual-cam bytes."""
    sink = FakeSink(64, 48)
    previews: list[np.ndarray] = []
    window = MainWindow(_enabled_config(64, 48))
    qtbot.addWidget(window)
    window.start_output(lambda d, w, h: sink)
    window.composite_view.show_frame = lambda frame: previews.append(frame)

    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    window._show_composite(
        frame,
        [
            Layer("pinout-crib", _stripe(SAFE, left=True)),
            Layer("engagement-identity", _stripe(SECRET, left=False), sensitive=True),
        ],
    )

    assert sink.frame_count == 1
    published = sink.frames[0]
    # The stream gets the safe layer but never the sensitive one.
    assert _has(published, SAFE)
    assert not _has(published, SECRET), "a sensitive layer reached the output sink"
    # The operator still sees everything locally.
    assert _has(previews[0], SAFE)
    assert _has(previews[0], SECRET)


def test_the_sink_frame_is_never_mutated_by_the_preview(qtbot) -> None:
    """Drawing the sensitive preview must not bleed into the already-sent frame."""
    sink = FakeSink(64, 48)
    window = MainWindow(_enabled_config(64, 48))
    qtbot.addWidget(window)
    window.start_output(lambda d, w, h: sink)

    window._show_composite(
        np.zeros((48, 64, 3), dtype=np.uint8),
        [Layer("secret", _stripe(SECRET, left=False), sensitive=True)],
    )

    assert not _has(sink.frames[0], SECRET)


def test_default_panels_are_not_sensitive() -> None:
    """The bench panels stay safe-by-default; only recon panels opt in."""
    from benchhud.shell.panel import Panel

    assert Panel.sensitive is False
