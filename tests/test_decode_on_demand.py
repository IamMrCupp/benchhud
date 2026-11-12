"""Decode only the scope frames the HUD takes (#346)."""

from __future__ import annotations

import time

import numpy as np

from benchhud.capture.camera import CameraFrameSource
from benchhud.capture.fallback import FallbackFrameSource
from benchhud.capture.latest_frame_reader import LatestFrameReader


class GrabCapture:
    """A camera that counts grabs and decodes; frame N is filled with N."""

    def __init__(self, frames: int = 1000) -> None:
        self.n = 0
        self.frames = frames
        self.grabs = 0
        self.retrieves = 0

    def isOpened(self) -> bool:
        return True

    def grab(self) -> bool:
        self.grabs += 1
        if self.n >= self.frames:
            return False
        self.n += 1
        return True

    def retrieve(self):
        self.retrieves += 1
        return True, np.full((4, 4, 3), self.n % 256, dtype=np.uint8)

    def read(self):
        if not self.grab():
            return False, None
        return self.retrieve()

    def get(self, _prop: int) -> float:
        return 4.0

    def release(self) -> None:
        pass


def _camera(capture) -> CameraFrameSource:
    return CameraFrameSource(0, capture_factory=lambda _d: capture)


def test_without_a_gate_every_frame_is_decoded() -> None:
    cap = GrabCapture()
    camera = _camera(cap)
    for _ in range(5):
        camera.read()
    assert cap.retrieves == 5 and camera.decodes == 5


def test_unwanted_frames_are_grabbed_but_not_decoded() -> None:
    cap = GrabCapture()
    camera = _camera(cap)
    wanted = iter([False, False, True, False])
    camera.set_decode_gate(lambda: next(wanted))
    first = camera.read()  # nothing decoded yet: always decodes
    assert cap.retrieves == 1
    assert camera.read() is first and camera.read() is first  # grabbed, skipped
    fresh = camera.read()  # wanted: the newest grabbed frame
    assert fresh is not first and int(fresh[0, 0, 0]) == 4
    assert camera.read() is fresh
    assert cap.grabs == 5 and cap.retrieves == 2 and camera.decodes == 2


def test_a_failed_grab_is_a_missed_frame_not_the_old_one() -> None:
    cap = GrabCapture(frames=1)
    camera = _camera(cap)
    camera.set_decode_gate(lambda: False)
    assert camera.read() is not None
    assert camera.read() is None  # the camera stopped: don't pretend it's live


def test_a_reopened_camera_decodes_its_first_frame() -> None:
    cap = GrabCapture()
    camera = _camera(cap)
    camera.set_decode_gate(lambda: False)
    camera.read()
    camera.open()
    camera.read()
    assert cap.retrieves == 2


def test_the_fallback_passes_the_gate_to_the_camera() -> None:
    cap = GrabCapture()
    camera = _camera(cap)
    source = FallbackFrameSource(camera, None, sleep=lambda _s: None)
    source.set_decode_gate(lambda: False)
    source.read()
    source.read()
    assert cap.grabs == 2 and cap.retrieves == 1


def test_the_reader_wants_a_decode_only_after_a_frame_is_taken() -> None:
    reader = LatestFrameReader(_camera(GrabCapture()))
    gate = reader._take_wanted
    assert gate() is True  # the first frame is always wanted
    assert gate() is False
    reader.latest()
    assert gate() is True and gate() is False


def test_a_live_reader_decodes_about_as_often_as_it_is_read() -> None:
    cap = GrabCapture(frames=10**9)
    reader = LatestFrameReader(_camera(cap))
    reader.start()
    takes = 0
    try:
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            if reader.latest() is not None:
                takes += 1
            time.sleep(0.02)
    finally:
        reader.stop()
    assert cap.grabs > 10 * cap.retrieves  # the thread spins through grabs
    assert cap.retrieves <= takes + 2  # but only decodes what was taken
