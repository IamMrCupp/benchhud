"""The composite -> virtual-camera path, offscreen.

Everything the bench needs is exercised here with a fake sink: only opening the
real loopback node is bench-gated.
"""

from __future__ import annotations

import threading
import time
from typing import ClassVar

import numpy as np

from benchhud.config import Config
from benchhud.output.sink import FakeSink
from benchhud.output.steady import SteadySink
from benchhud.shell.main_window import MainWindow


class _RecordingSink(FakeSink):
    """A FakeSink that remembers how it was opened."""

    opened: ClassVar[list[tuple[str, int, int]]] = []

    @classmethod
    def factory(cls, device: str, width: int, height: int) -> FakeSink:
        sink = cls(width, height)
        cls.opened.append((device, width, height))
        return sink


class _ExplodingSink(FakeSink):
    """Stands in for a loopback node that dies mid-stream."""

    def send(self, frame: np.ndarray) -> None:
        raise OSError("device went away")


def _enabled_config(width: int = 64, height: int = 48) -> Config:
    config = Config()
    config.output.enabled = True
    config.output.device = "/dev/video10"
    config.output.width = width
    config.output.height = height
    return config


def _window(qtbot, config: Config) -> MainWindow:
    window = MainWindow(config)
    qtbot.addWidget(window)
    return window


def test_output_stays_shut_when_disabled(qtbot) -> None:
    """Default config: no sink is opened, so nothing touches a device."""
    opened: list[tuple[str, int, int]] = []
    window = _window(qtbot, Config())

    window.start_output(lambda d, w, h: opened.append((d, w, h)) or FakeSink(w, h))

    assert opened == []


def test_start_output_opens_the_configured_device(qtbot) -> None:
    _RecordingSink.opened = []
    window = _window(qtbot, _enabled_config(320, 240))

    window.start_output(_RecordingSink.factory)

    assert _RecordingSink.opened == [("/dev/video10", 320, 240)]


def test_start_output_is_idempotent(qtbot) -> None:
    _RecordingSink.opened = []
    window = _window(qtbot, _enabled_config())

    window.start_output(_RecordingSink.factory)
    window.start_output(_RecordingSink.factory)

    assert len(_RecordingSink.opened) == 1


def test_composite_frames_reach_the_sink(qtbot) -> None:
    """The point of the whole seam: what's on screen is what OBS gets."""
    sink = FakeSink(64, 48)
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: sink)

    window.scope_panel.update_frame(np.full((48, 64, 3), 7, dtype=np.uint8))
    window.render_composite()

    assert sink.frame_count == 1
    assert sink.frames[0].shape == (48, 64, 3)


def test_frames_are_resized_to_the_sink(qtbot) -> None:
    """A 1080p scope must not write short frames into a 720p loopback."""
    sink = FakeSink(64, 48)
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: sink)

    window.scope_panel.update_frame(np.zeros((216, 384, 3), dtype=np.uint8))
    window.render_composite()

    assert sink.frames[0].shape == (48, 64, 3)


def test_a_failing_sink_is_dropped_not_raised(qtbot) -> None:
    """A dead device must not raise into the Qt slot on every single frame."""
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: _ExplodingSink(64, 48))

    window.scope_panel.update_frame(np.zeros((48, 64, 3), dtype=np.uint8))
    window.render_composite()  # must not raise
    window.render_composite()

    assert window._sink is None


def test_stop_output_closes_and_detaches(qtbot) -> None:
    sink = FakeSink(64, 48)
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: sink)

    window.stop_output()
    window.stop_output()  # idempotent
    window.scope_panel.update_frame(np.zeros((48, 64, 3), dtype=np.uint8))
    window.render_composite()

    assert sink.closed is True
    assert sink.frame_count == 0


def test_restart_output_applies_a_disabled_checkbox(qtbot) -> None:
    """Unticking output in Preferences takes effect without a restart."""
    sink = FakeSink(64, 48)
    config = _enabled_config(64, 48)
    window = _window(qtbot, config)
    window.start_output(lambda d, w, h: sink)

    config.output.enabled = False
    window.restart_output()

    assert sink.closed is True
    assert window._sink is None


def test_close_event_releases_the_sink(qtbot) -> None:
    sink = FakeSink(64, 48)
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: sink)

    window.close()

    assert sink.closed is True


class _StallingCamera:
    """Hands over one frame, then never another until it's closed."""

    def __init__(self) -> None:
        self._sent = False
        self._closed = threading.Event()

    def read(self) -> np.ndarray | None:
        if not self._sent:
            self._sent = True
            return np.full((48, 64, 3), 9, dtype=np.uint8)
        self._closed.wait(0.05)
        return None

    def close(self) -> None:
        self._closed.set()


def test_a_stalled_camera_does_not_stall_the_output(qtbot) -> None:
    """The frame ticks keep re-sending the last frame while the camera is silent (#566)."""
    sink = FakeSink(64, 48)
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: sink)
    window.start_preview(_StallingCamera)
    try:
        qtbot.waitUntil(lambda: sink.frame_count >= 5, timeout=3000)
    finally:
        window.stop_preview()

    assert all(int(frame[0, 0, 0]) == 9 for frame in sink.frames)


def test_a_busy_gui_thread_does_not_stall_the_loopback(qtbot) -> None:
    """Writes go on while the GUI thread is stuck, as they do on the bench (#566)."""
    inner = FakeSink(64, 48)
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: SteadySink(inner, repeat_after=0.03))
    window.scope_panel.update_frame(np.full((48, 64, 3), 5, dtype=np.uint8))
    window.render_composite()
    qtbot.waitUntil(lambda: inner.frame_count >= 1, timeout=2000)

    before = inner.frame_count
    time.sleep(0.4)  # the GUI thread is busy: no ticks, no new frames
    written = inner.frame_count - before
    window.stop_output()

    assert written >= 4
    assert inner.closed is True


class _DeadLoopback(FakeSink):
    """A loopback node that's gone: every write fails."""

    def send(self, frame: np.ndarray) -> None:
        raise OSError("device went away")


def test_a_write_that_fails_on_the_writer_thread_drops_the_sink(qtbot) -> None:
    """The writer's error comes back through the next frame and closes the output."""
    inner = _DeadLoopback(64, 48)
    window = _window(qtbot, _enabled_config(64, 48))
    window.start_output(lambda d, w, h: SteadySink(inner, repeat_after=0.03))
    window.scope_panel.update_frame(np.full((48, 64, 3), 5, dtype=np.uint8))
    window.render_composite()
    qtbot.waitUntil(lambda: window._sink is not None and _failed(window), timeout=2000)
    window.render_composite()  # must not raise

    assert window._sink is None
    assert inner.closed is True


def _failed(window: MainWindow) -> bool:
    try:
        window._sink.send(np.zeros((48, 64, 3), dtype=np.uint8))
    except OSError:
        return True
    return False
