"""Y16Camera: raw Y16 in, °C out, and an honest refusal for anything else."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.capture.fallback import FallbackFrameSource
from benchhud.capture.thermal import NOT_Y16, Y16_FOURCC, Y16Camera, raw_y16
from benchhud.thermal.convert import celsius_to_y16

W, H = 192, 256


class FakeThermalCapture:
    """cv2.VideoCapture stand-in that records format requests."""

    def __init__(self, frame, *, opened: bool = True, size=(W, H)) -> None:
        self.frame = frame
        self.opened = opened
        self.size = size
        self.props: dict[int, float] = {}
        self.released = False

    def isOpened(self) -> bool:  # cv2's spelling
        return self.opened

    def set(self, prop_id: int, value: float) -> bool:
        self.props[prop_id] = value
        return True

    def get(self, prop_id: int) -> float:
        return {3: self.size[0], 4: self.size[1]}.get(prop_id, 0.0)

    def read(self):
        return (self.frame is not None, self.frame)

    def release(self) -> None:
        self.released = True


def _camera(frame, **kwargs):
    capture = FakeThermalCapture(frame, **kwargs)
    return Y16Camera("/dev/video6", capture_factory=lambda _d: capture), capture


def test_requests_raw_y16_with_rgb_conversion_off():
    _cam, capture = _camera(None)
    assert capture.props[6] == Y16_FOURCC  # CAP_PROP_FOURCC
    assert capture.props[16] == 0  # CAP_PROP_CONVERT_RGB
    assert (capture.props[3], capture.props[4]) == (W, H)


def test_y16_fourcc_keeps_the_trailing_space():
    assert Y16_FOURCC.to_bytes(4, "little") == b"Y16 "


def test_uint16_frame_becomes_celsius():
    temps = np.full((H, W), 42.5, dtype=np.float32)
    cam, _ = _camera(celsius_to_y16(temps))
    out = cam.read()
    assert out is not None and out.dtype == np.float32
    assert out.shape == (H, W)
    assert np.allclose(out, 42.5, atol=0.02)
    assert cam.fatal_error is None


def test_byte_buffer_is_reinterpreted_as_little_endian_y16():
    raw = celsius_to_y16(np.full((H, W), 30.0, dtype=np.float32))
    buffer = raw.astype("<u2").view(np.uint8).reshape(1, -1)
    assert raw_y16(buffer, (W, H)).shape == (H, W)
    assert np.array_equal(raw_y16(buffer, (W, H)), raw)


def test_single_channel_uint16_is_accepted():
    raw = np.zeros((H, W, 1), dtype=np.uint16)
    assert raw_y16(raw, (W, H)).shape == (H, W)


@pytest.mark.parametrize(
    "frame",
    [
        np.zeros((H, W, 3), dtype=np.uint8),  # an ordinary webcam's BGR
        np.zeros((720, 1280, 3), dtype=np.uint8),
        np.zeros((H, W), dtype=np.uint8),  # 8-bit grey: 1 byte/pixel
    ],
)
def test_anything_but_y16_is_refused_not_guessed(frame):
    """Reading 8-bit pixels as Kelvin x 64 would make up temperatures."""
    cam, capture = _camera(frame)
    assert cam.read() is None
    assert cam.fatal_error == NOT_Y16
    assert capture.released
    assert not cam.is_open


def test_camera_that_will_not_open():
    cam, _ = _camera(None, opened=False)
    assert not cam.is_open
    assert cam.fatal_error is None  # retryable: maybe it's just unplugged


def test_fallback_source_reports_a_non_thermal_camera_and_stops_retrying():
    """A webcam picked as thermal: say so once, don't reopen it every 3 s."""
    capture = FakeThermalCapture(np.zeros((H, W, 3), dtype=np.uint8))
    opens: list[int] = []

    def factory(_device):
        opens.append(1)
        return capture

    now = [0.0]
    source = FallbackFrameSource(
        Y16Camera(0, capture_factory=factory),
        None,
        name="Eris Camera",
        clock=lambda: now[0],
        sleep=lambda _s: None,
    )
    assert source.read() is None
    assert source.status.reason == f"Eris Camera {NOT_Y16}"
    for t in (5.0, 10.0, 60.0):
        now[0] = t
        assert source.read() is None
    assert len(opens) == 1
    assert source.status.reason == f"Eris Camera {NOT_Y16}"


def test_no_fallback_means_no_stand_in_frames():
    source = FallbackFrameSource(None, None, sleep=lambda _s: None)
    assert source.read() is None
    assert source.size == (0, 0)
