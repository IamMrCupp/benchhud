"""Time the per-frame display and loopback paths (#347, #351).

Not a test: pytest doesn't collect it. Run it by hand, before and after a
change to the Qt views or the loopback writer:

    QT_QPA_PLATFORM=offscreen uv run python tests/bench_frame_paths.py

It prints milliseconds per frame for each path, the median of several rounds.
The numbers are for comparing two builds on one machine, not a bench fps.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Callable

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from benchhud.output import v4l2
from benchhud.shell.composite_view import CompositeView
from benchhud.shell.panels.scope import ScopePanel
from benchhud.shell.panels.thermal import ThermalPanel

ROUNDS = 7
FRAMES = 60


def _frames(width: int, height: int, count: int = 4) -> list[np.ndarray]:
    rng = np.random.default_rng(347)
    return [rng.integers(0, 256, (height, width, 3), dtype=np.uint8) for _ in range(count)]


def _time(step: Callable[[int], None]) -> float:
    """Median ms per call over ROUNDS rounds of FRAMES calls."""
    rounds = []
    for _ in range(ROUNDS):
        start = time.perf_counter()
        for i in range(FRAMES):
            step(i)
        rounds.append((time.perf_counter() - start) * 1000 / FRAMES)
    return statistics.median(rounds)


class _DevNull:
    """A loopback node that takes the write and throws it away."""

    def __init__(self, pixelformat: int | None) -> None:
        if pixelformat is not None:
            self.pixelformat = pixelformat
        self._fd = os.open(os.devnull, os.O_WRONLY)

    def write(self, data) -> None:
        os.write(self._fd, data)

    def close(self) -> None:
        os.close(self._fd)


def _sink(width: int, height: int, pixelformat: int | None) -> v4l2.V4l2LoopbackSink:
    return v4l2.V4l2LoopbackSink(
        "/dev/null", width, height, device_factory=lambda d, w, h: _DevNull(pixelformat)
    )


def main() -> None:
    app = QApplication.instance() or QApplication([])
    results: list[tuple[str, float]] = []

    full = _frames(1920, 1080)
    out = _frames(1280, 720)
    temps = [
        np.random.default_rng(i).uniform(20, 90, (256, 192)).astype(np.float32) for i in (1, 2)
    ]

    scope = ScopePanel()
    scope.resize(480, 270)
    scope.show()
    app.processEvents()
    results.append(
        ("scope panel, 1080p frame, 480x270 dock", _time(lambda i: scope.update_frame(full[i % 4])))
    )
    scope.hide()
    app.processEvents()
    results.append(
        ("scope panel, 1080p frame, dock hidden", _time(lambda i: scope.update_frame(full[i % 4])))
    )

    view = CompositeView()
    view.resize(1100, 620)
    view.show()
    app.processEvents()
    results.append(
        ("composite view, 720p frame, 1100x620 view", _time(lambda i: view.show_frame(out[i % 4])))
    )

    thermal = ThermalPanel()
    thermal.resize(300, 400)
    thermal.show()
    app.processEvents()
    results.append(
        (
            "thermal panel, 192x256, 300x400 dock",
            _time(lambda i: thermal.update_temps(temps[i % 2])),
        )
    )

    rgb = _sink(1280, 720, getattr(v4l2, "V4L2_PIX_FMT_RGB24", None))
    results.append(("loopback send, 720p, node takes RGB3", _time(lambda i: rgb.send(out[i % 4]))))
    bgr_format = getattr(v4l2, "V4L2_PIX_FMT_BGR24", None)
    if bgr_format is not None:
        bgr = _sink(1280, 720, bgr_format)
        results.append(
            ("loopback send, 720p, node takes BGR3", _time(lambda i: bgr.send(out[i % 4])))
        )

    for name, ms in results:
        print(f"{name:45s} {ms:7.3f} ms/frame")


if __name__ == "__main__":
    main()
