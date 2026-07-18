"""Tests for the threaded latest-frame reader."""

from __future__ import annotations

import time

import numpy as np

from benchhud.capture.frame_source import SyntheticFrameSource
from benchhud.capture.latest_frame_reader import LatestFrameReader


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class _FailingSource:
    """A FrameSource whose read() raises, to exercise error capture."""

    size = (16, 16)

    def __init__(self) -> None:
        self.closed = False

    def read(self) -> np.ndarray | None:
        raise RuntimeError("boom")

    def close(self) -> None:
        self.closed = True


def test_latest_is_none_before_start() -> None:
    reader = LatestFrameReader(SyntheticFrameSource())
    assert reader.latest() is None


def test_captures_frames() -> None:
    reader = LatestFrameReader(SyntheticFrameSource(64, 48), poll_interval=0.001)
    reader.start()
    try:
        assert _wait_until(lambda: reader.latest() is not None)
        frame = reader.latest()
        assert frame is not None
        assert frame.shape == (48, 64, 3)
    finally:
        reader.stop()
    assert not reader.is_running


def test_context_manager_runs_and_stops() -> None:
    src = SyntheticFrameSource(32, 32)
    with LatestFrameReader(src, poll_interval=0.001) as reader:
        assert _wait_until(lambda: reader.latest() is not None)
    assert not reader.is_running


def test_stop_is_idempotent_and_closes_source() -> None:
    src = SyntheticFrameSource()
    reader = LatestFrameReader(src)
    reader.start()
    reader.stop()
    reader.stop()  # must not raise
    assert src.read() is None  # source was closed


def test_stop_can_leave_source_open() -> None:
    src = SyntheticFrameSource()
    reader = LatestFrameReader(src)
    reader.start()
    reader.stop(close_source=False)
    assert src.read() is not None  # still usable


def test_error_is_captured() -> None:
    src = _FailingSource()
    reader = LatestFrameReader(src)
    reader.start()
    assert _wait_until(lambda: reader.error is not None)
    assert isinstance(reader.error, RuntimeError)
    assert _wait_until(lambda: not reader.is_running)
    reader.stop()
