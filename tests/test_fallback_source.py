"""FallbackFrameSource: prefer the camera, fall back honestly, keep retrying."""

from __future__ import annotations

import itertools

import numpy as np

from benchhud.capture.fallback import FallbackFrameSource, SourceStatus
from benchhud.capture.frame_source import SyntheticFrameSource

CAMERA_FRAME = np.full((4, 6, 3), 7, dtype=np.uint8)


class FakeCamera:
    """Scriptable camera: ``openable`` gates open(), ``frames`` gates read()."""

    def __init__(self, *, openable: bool = True, frames: bool = True) -> None:
        self.openable = openable
        self.frames = frames
        self.open_calls = 0
        self.closed = False
        self._open = False
        self.open()

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def size(self) -> tuple[int, int]:
        return (6, 4)

    def open(self) -> bool:
        self.open_calls += 1
        self._open = self.openable
        return self._open

    def read(self):
        return CAMERA_FRAME if (self._open and self.frames) else None

    def release(self) -> None:
        self._open = False

    def close(self) -> None:
        self.release()
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def _source(camera, clock, **kwargs) -> FallbackFrameSource:
    return FallbackFrameSource(
        camera,
        SyntheticFrameSource(8, 8),
        name="Scope Cam",
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )


def test_live_camera_frames_pass_through():
    clock = Clock()
    source = _source(FakeCamera(), clock)
    assert source.read() is CAMERA_FRAME
    assert source.status == SourceStatus(True)
    assert source.size == (6, 4)


def test_not_live_until_the_first_frame_arrives():
    """An open camera that hasn't delivered yet isn't claimed as live."""
    source = _source(FakeCamera(frames=False), Clock())
    assert not source.status.live


def test_unopenable_camera_serves_the_pattern_and_says_why():
    clock = Clock()
    source = _source(FakeCamera(openable=False), clock, open_hint="allow it in Settings")
    frame = source.read()
    assert frame is not None and frame.shape == (8, 8, 3)
    assert source.status == SourceStatus(False, "couldn't open Scope Cam — allow it in Settings")
    assert source.size == (8, 8)


def test_picks_the_camera_up_once_the_os_allows_it():
    """macOS: the first open fails behind the permission prompt; Allow must not need a relaunch."""
    clock = Clock()
    camera = FakeCamera(openable=False)
    source = _source(camera, clock, retry_interval=3.0)

    source.read()
    assert camera.open_calls == 1  # no retry before the interval

    camera.openable = True  # operator clicks Allow
    clock.now = 3.0
    assert source.read() is CAMERA_FRAME
    assert source.status.live
    assert camera.open_calls == 2


def test_retries_are_rate_limited():
    clock = Clock()
    camera = FakeCamera(openable=False)
    source = _source(camera, clock, retry_interval=3.0)
    for step in range(11):  # t = 0.0 .. 5.0, reading every half second
        clock.now = step * 0.5
        source.read()
    assert camera.open_calls == 2  # constructor + one retry at t = 3.0


def test_brief_dropouts_hold_rather_than_flash_the_pattern():
    """A dropped frame or NUC shutter stall returns None (the panel keeps its last frame)."""
    clock = Clock()
    camera = FakeCamera()
    source = _source(camera, clock, stall_timeout=3.0)
    source.read()
    camera.frames = False
    clock.now = 2.0
    assert source.read() is None
    assert source.status.live


def test_a_stalled_camera_is_released_and_retried():
    """Unplugged mid-session: OpenCV still says open, but frames never come."""
    clock = Clock()
    camera = FakeCamera()
    source = _source(camera, clock, stall_timeout=3.0, retry_interval=3.0)
    source.read()

    camera.frames = False
    clock.now = 3.5
    frame = source.read()
    assert frame is not None and frame.shape == (8, 8, 3)  # pattern, not frozen
    assert not camera.is_open
    assert source.status == SourceStatus(False, "Scope Cam stopped sending frames, reconnecting…")

    camera.frames = True  # plugged back in
    clock.now = 7.0
    assert source.read() is CAMERA_FRAME
    assert source.status.live


def test_stall_reason_survives_until_a_retry_actually_fails():
    clock = Clock()
    camera = FakeCamera()
    source = _source(camera, clock, stall_timeout=1.0, retry_interval=3.0)
    source.read()
    camera.frames = False
    clock.now = 1.5
    source.read()
    clock.now = 2.0
    source.read()
    assert source.status.reason == "Scope Cam stopped sending frames, reconnecting…"

    camera.openable = False
    clock.now = 5.0
    source.read()
    assert source.status.reason.startswith("couldn't open Scope Cam")


def test_no_camera_selected():
    source = _source(None, Clock())
    assert source.read() is not None
    assert source.status == SourceStatus(False, "no camera selected")


def test_fallback_is_paced_so_the_reader_thread_does_not_spin():
    clock = Clock()
    source = _source(None, clock, fallback_fps=10.0)
    source.read()
    assert clock.slept == [0.1]


def test_close_closes_the_camera():
    camera = FakeCamera()
    source = _source(camera, Clock())
    source.close()
    assert camera.closed


def test_failed_reopens_back_off_and_reset_on_a_frame():
    """A camera that crashes if restarted mid-pause gets ever longer to recover (#276)."""
    clock = Clock()
    camera = FakeCamera()
    source = _source(camera, clock, stall_timeout=1.0, retry_interval=5.0, max_retry_interval=20.0)
    source.read()
    camera.frames = False
    clock.now = 1.5
    source.read()  # given up on: released, retry at 6.5
    camera.openable = False
    opens = camera.open_calls
    attempts = []
    for tick in range(0, 800):
        clock.now = 1.5 + tick * 0.1
        source.read()
        if camera.open_calls != opens:
            opens = camera.open_calls
            attempts.append(round(clock.now, 1))
    gaps = [round(b - a, 1) for a, b in itertools.pairwise(attempts)]
    assert gaps[:3] == [10.0, 20.0, 20.0]  # doubled, then capped

    camera.openable = True
    camera.frames = True
    clock.now += 30
    source.read()
    assert source.status.live
    camera.frames = False
    clock.now += 1.5
    source.read()  # stalls again: back to the short wait
    camera.openable = False
    start = clock.now
    while camera.open_calls == opens + 1:
        clock.now += 0.1
        source.read()
    assert 5.0 <= clock.now - start < 5.2


def test_stalled_for_and_lost_for():
    clock = Clock()
    camera = FakeCamera()
    source = _source(camera, clock, stall_timeout=3.0)
    source.read()
    assert source.stalled_for == 0.0 and source.lost_for == 0.0
    camera.frames = False
    clock.now = 2.0
    source.read()
    assert source.stalled_for == 2.0
    clock.now = 3.5
    source.read()  # given up on
    assert source.stalled_for == 0.0
    clock.now = 10.0
    assert source.lost_for == 6.5
    camera.frames = True
    source.read()
    assert source.lost_for == 0.0
