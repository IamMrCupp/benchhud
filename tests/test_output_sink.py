"""Tests for output sinks."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.capture.frame_source import SyntheticFrameSource
from benchhud.output.sink import FakeSink, OutputSink, VirtualCamSink


class _FakeCamera:
    """Stand-in for pyvirtualcam.Camera: records the RGB frames it's sent."""

    def __init__(self, width: int, height: int, fps: int) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.sent: list[np.ndarray] = []
        self.closed = False

    def send(self, frame: np.ndarray) -> None:
        self.sent.append(frame)

    def close(self) -> None:
        self.closed = True


def test_fake_sink_satisfies_protocol() -> None:
    assert isinstance(FakeSink(), OutputSink)


def test_fake_sink_records_frames() -> None:
    sink = FakeSink(64, 48)
    assert sink.size == (64, 48)
    sink.send(SyntheticFrameSource(64, 48).read())
    assert sink.frame_count == 1


def test_fake_sink_send_after_close_raises() -> None:
    sink = FakeSink()
    sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        sink.send(np.zeros((4, 4, 3), dtype=np.uint8))


def test_virtualcam_satisfies_protocol() -> None:
    sink = VirtualCamSink(64, 48, camera_factory=_FakeCamera)
    assert isinstance(sink, OutputSink)


def test_virtualcam_converts_bgr_to_rgb() -> None:
    cam = _FakeCamera(2, 2, 15)
    sink = VirtualCamSink(2, 2, camera_factory=lambda w, h, f: cam)

    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # pure blue in BGR
    sink.send(frame)

    assert len(cam.sent) == 1
    rgb = cam.sent[0]
    # BGR blue -> RGB (0, 0, 255)
    assert tuple(rgb[0, 0]) == (0, 0, 255)


def test_virtualcam_close_closes_camera() -> None:
    cam = _FakeCamera(2, 2, 15)
    sink = VirtualCamSink(2, 2, camera_factory=lambda w, h, f: cam)
    sink.close()
    assert cam.closed is True


def test_frame_flows_source_to_sink() -> None:
    """AT3 (hardware-free): a frame flows source -> sink."""
    source = SyntheticFrameSource(32, 32)
    sink = FakeSink(32, 32)
    for _ in range(3):
        frame = source.read()
        assert frame is not None
        sink.send(frame)
    assert sink.frame_count == 3
