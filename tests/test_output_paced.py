"""The loopback keeps being written while the GUI thread or a camera stalls (#566).

OBS times out on a v4l2 node after five frame periods with nothing new (166 ms
on the bench). These pin that a held-up GUI thread, a stalled camera, or a slow
open on the GUI thread can't leave the node that quiet, and that a long gap
gets a log line.

Real threads, real time: the bounds are loose so a busy CI runner can't fail
them, and still far under the seconds-long silences being guarded against.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time

import numpy as np
import pytest

from benchhud.config import Config
from benchhud.output.paced import REPEAT_AFTER_S, PacedSink
from benchhud.output.sink import FakeSink
from benchhud.output.v4l2 import V4L2_PIX_FMT_BGR24, V4l2LoopbackSink
from benchhud.shell.main_window import MainWindow, paced

W, H = 8, 6


def _frame(value: int = 0) -> np.ndarray:
    return np.full((H, W, 3), value, dtype=np.uint8)


def _wait_for(condition, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class _TimedSink(FakeSink):
    """A FakeSink that notes when each frame was written, and by which thread."""

    def __init__(self, width: int = W, height: int = H) -> None:
        super().__init__(width, height)
        self.times: list[float] = []
        self.threads: set[str] = set()

    def send(self, frame: np.ndarray) -> None:
        super().send(frame)
        self.times.append(time.monotonic())
        self.threads.add(threading.current_thread().name)


class _Device:
    """A loopback node that records when it was written."""

    pixelformat = V4L2_PIX_FMT_BGR24

    def __init__(self) -> None:
        self.times: list[float] = []
        self.closed = False

    def write(self, data) -> None:
        self.times.append(time.monotonic())

    def close(self) -> None:
        self.closed = True


def _max_gap(times: list[float]) -> float:
    return max((b - a for a, b in itertools.pairwise(times)), default=0.0)


# --- PacedSink on its own -----------------------------------------------------


def test_a_new_frame_is_written_by_the_writer_thread() -> None:
    inner = _TimedSink()
    sink = PacedSink(inner, name="test")
    try:
        sink.send(_frame(7))
        assert _wait_for(lambda: inner.frame_count >= 1)
        assert inner.frames[0][0, 0, 0] == 7
        assert inner.threads == {"paced:test"}
    finally:
        sink.close()


def test_the_last_frame_is_repeated_while_no_new_one_comes() -> None:
    """A stalled composite (camera, GUI thread): the node still gets a frame
    every REPEAT_AFTER_S, so a reader never times out on it."""
    inner = _TimedSink()
    sink = PacedSink(inner, repeat_after=0.02)
    try:
        sink.send(_frame(3))
        assert _wait_for(lambda: inner.frame_count >= 10)
        assert sink.repeats >= 9
        assert all(f[0, 0, 0] == 3 for f in inner.frames)
        assert _max_gap(inner.times) < 0.5
    finally:
        sink.close()


def test_send_never_waits_on_a_slow_device() -> None:
    """The GUI thread hands the frame over and moves on, however long the
    write takes."""
    release = threading.Event()

    class _SlowSink(FakeSink):
        def send(self, frame: np.ndarray) -> None:
            release.wait(5)
            super().send(frame)

    inner = _SlowSink(W, H)
    sink = PacedSink(inner)
    try:
        start = time.monotonic()
        for i in range(5):
            sink.send(_frame(i))
        assert time.monotonic() - start < 0.5
        release.set()
        # The write in progress finishes, then the newest frame goes; the
        # ones in between were replaced before they were written.
        assert _wait_for(lambda: inner.frame_count >= 2 and inner.frames[-1][0, 0, 0] == 4)
    finally:
        release.set()
        sink.close()


def test_the_frame_is_copied_when_handed_over() -> None:
    """The caller may draw on its array afterwards; the written frame is the one sent."""
    inner = _TimedSink()
    sink = PacedSink(inner, repeat_after=0.02)
    try:
        frame = _frame(1)
        sink.send(frame)
        frame[:] = 99
        assert _wait_for(lambda: inner.frame_count >= 3)
        assert all(f[0, 0, 0] == 1 for f in inner.frames)
    finally:
        sink.close()


def test_a_failed_write_surfaces_on_the_next_send(caplog) -> None:
    """The window drops a sink whose send raises; that has to still happen."""

    class _Broken(FakeSink):
        def send(self, frame: np.ndarray) -> None:
            raise OSError("device went away")

    sink = PacedSink(_Broken(W, H), name="broken")
    try:
        with caplog.at_level(logging.ERROR, logger="benchhud.output.paced"):
            sink.send(_frame())
            assert _wait_for(lambda: sink._error is not None)
        with pytest.raises(RuntimeError, match="write failed"):
            sink.send(_frame())
        assert "broken: write failed" in caplog.text
    finally:
        sink.close()


def test_close_stops_the_writer_and_closes_the_device() -> None:
    inner = _TimedSink()
    sink = PacedSink(inner, repeat_after=0.01)
    sink.send(_frame())
    sink.close()
    assert inner.closed
    assert not sink._thread.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        sink.send(_frame())
    sink.close()  # idempotent


def test_a_long_gap_between_writes_is_logged_with_its_length(caplog) -> None:
    """A write that took 2.5 s (a blocked device, a starved writer) gets a line
    saying how long the node went without one, to match against OBS's log."""
    now = [100.0]
    slow = threading.Event()

    class _Stuck(FakeSink):
        def send(self, frame: np.ndarray) -> None:
            if slow.is_set():
                now[0] += 2.5
            super().send(frame)

    inner = _Stuck(W, H)
    sink = PacedSink(inner, name="/dev/video10", clock=lambda: now[0])
    try:
        with caplog.at_level(logging.WARNING, logger="benchhud.output.paced"):
            sink.send(_frame(1))
            assert _wait_for(lambda: inner.frame_count == 1)
            slow.set()
            sink.send(_frame(2))
            assert _wait_for(lambda: inner.frame_count == 2)
            assert _wait_for(lambda: "without a write" in caplog.text)
        assert "/dev/video10: 2.5 s without a write" in caplog.text
    finally:
        sink.close()


def test_a_long_wait_for_a_new_frame_is_logged_with_its_length(caplog) -> None:
    now = [100.0]
    inner = _TimedSink()
    sink = PacedSink(inner, name="/dev/video10", clock=lambda: now[0])
    try:
        with caplog.at_level(logging.WARNING, logger="benchhud.output.paced"):
            sink.send(_frame(1))
            assert _wait_for(lambda: inner.frame_count == 1)
            now[0] += 3.2  # the composite stopped coming for 3.2 s
            sink.send(_frame(2))
            assert _wait_for(lambda: "no new frame" in caplog.text)
        assert "/dev/video10: no new frame for 3.2 s" in caplog.text
    finally:
        sink.close()


def test_short_gaps_are_not_logged(caplog) -> None:
    inner = _TimedSink()
    sink = PacedSink(inner, repeat_after=0.01)
    try:
        with caplog.at_level(logging.WARNING, logger="benchhud.output.paced"):
            for i in range(5):
                sink.send(_frame(i))
                time.sleep(0.02)
        assert caplog.text == ""
    finally:
        sink.close()


def test_the_default_repeat_beats_obs_timeout() -> None:
    """OBS's timeout on the bench's nodes: five periods at 30 fps."""
    assert REPEAT_AFTER_S < 5 / 30


# --- In the window ------------------------------------------------------------


def _loopback_config() -> Config:
    config = Config()
    config.output.enabled = True
    config.output.device = "/dev/video10"
    config.output.width = W
    config.output.height = H
    config.output.scope_device = ""
    config.output.thermal_device = ""
    return config


def _loopback_window(qtbot, config: Config) -> tuple[MainWindow, dict[str, _Device]]:
    devices: dict[str, _Device] = {}

    def factory(device: str, width: int, height: int) -> V4l2LoopbackSink:
        node = devices.setdefault(device, _Device())
        return V4l2LoopbackSink(device, width, height, device_factory=lambda d, w, h: node)

    window = MainWindow(config)
    qtbot.addWidget(window)
    window._linux_output = True
    window.start_output(factory)
    return window, devices


def test_only_loopback_sinks_are_paced() -> None:
    fake = FakeSink(W, H)
    assert paced(fake) is fake
    node = _Device()
    loopback = V4l2LoopbackSink("/dev/x", W, H, device_factory=lambda d, w, h: node)
    wrapped = paced(loopback)
    try:
        assert isinstance(wrapped, PacedSink)
        assert wrapped.inner is loopback
    finally:
        wrapped.close()


def test_a_held_up_gui_thread_does_not_stall_the_loopback(qtbot) -> None:
    """The bench's case: the GUI thread busy (paint, telemetry, a loaded
    machine) for longer than OBS waits. The node is written all through it."""
    window, devices = _loopback_window(qtbot, _loopback_config())
    try:
        assert isinstance(window._sink, PacedSink)
        window._publish_frame(_frame(5))
        node = devices["/dev/video10"]
        assert _wait_for(lambda: len(node.times) >= 1)
        start = time.monotonic()
        time.sleep(0.6)  # the GUI thread, held up
        during = [t for t in node.times if t >= start]
        assert len(during) >= 3
        assert _max_gap([start, *during]) < 0.4
    finally:
        window.stop_output()
    assert devices["/dev/video10"].closed


def test_a_stalled_camera_does_not_stall_the_loopback(qtbot) -> None:
    """The scope camera gives one frame, then its read blocks (a stalled USB
    camera, or a reopen that hangs): the output keeps going out."""
    stalled = threading.Event()
    release = threading.Event()

    class _StallingSource:
        def __init__(self) -> None:
            self.calls = 0

        def read(self):
            self.calls += 1
            if self.calls > 1:
                stalled.set()
                release.wait(10)
                return None
            return _frame(9)

        def close(self) -> None:
            release.set()

    window, devices = _loopback_window(qtbot, _loopback_config())
    try:
        window.start_preview(_StallingSource)
        node = devices["/dev/video10"]
        assert _wait_for(stalled.is_set)
        qtbot.waitUntil(lambda: len(node.times) >= 1, timeout=3000)
        start = time.monotonic()
        qtbot.wait(600)
        during = [t for t in node.times if t >= start]
        assert len(during) >= 3
        assert _max_gap([start, *during]) < 0.4
    finally:
        release.set()
        window.stop_preview()
        window.stop_output()


def test_a_slow_open_on_the_gui_thread_does_not_stall_the_loopback(qtbot) -> None:
    """The thermal feed's node is opened on the GUI thread on the thermal's
    first frame. An open that hangs there leaves the main output written."""
    config = _loopback_config()
    config.output.thermal_device = "/dev/video13"
    window, devices = _loopback_window(qtbot, config)
    try:
        window._publish_frame(_frame(2))
        node = devices["/dev/video10"]
        assert _wait_for(lambda: len(node.times) >= 1)
        start = time.monotonic()

        def slow_open(device: str, width: int, height: int) -> FakeSink:
            time.sleep(0.6)
            return FakeSink(width, height)

        window._feed_factory = slow_open
        window._thermal_feed_sink(np.zeros((192, 256), dtype=np.float32))
        during = [t for t in node.times if t >= start]
        assert len(during) >= 3
        assert _max_gap([start, *during]) < 0.4
    finally:
        window.stop_output()
