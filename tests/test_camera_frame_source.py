"""CameraFrameSource — exercised with a fake capture, no hardware required."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.capture.camera import CameraFrameSource, parse_device
from benchhud.capture.frame_source import FrameSource


class FakeCapture:
    def __init__(self, *, opened: bool = True, frames: list | None = None, size=(640, 480)):
        self._opened = opened
        self._frames = list(frames if frames is not None else [_frame()])
        self._size = size
        self.released = False

    def isOpened(self) -> bool:  # cv2's spelling
        return self._opened

    def read(self):
        if not self._frames:
            return False, None
        return True, self._frames.pop(0)

    def get(self, prop_id: int) -> float:
        return float(self._size[0] if prop_id == 3 else self._size[1])

    def release(self) -> None:
        self.released = True
        self._opened = False


def _frame(width: int = 640, height: int = 480) -> np.ndarray:
    return np.full((height, width, 3), 7, dtype=np.uint8)


def _source(capture) -> CameraFrameSource:
    return CameraFrameSource(0, capture_factory=lambda _device: capture)


@pytest.mark.parametrize(
    ("given", "expected"),
    [("0", 0), (0, 0), ("2", 2), ("/dev/video4", "/dev/video4"), (" 1 ", 1)],
)
def test_parse_device(given, expected):
    assert parse_device(given) == expected


def test_satisfies_the_frame_source_protocol():
    assert isinstance(_source(FakeCapture()), FrameSource)


def test_reads_a_frame_and_reports_size():
    source = _source(FakeCapture())
    frame = source.read()
    assert frame is not None
    assert frame.shape == (480, 640, 3)
    assert source.size == (640, 480)
    assert source.is_open
    assert source.last_error is None


def test_size_comes_from_the_frame_when_the_driver_lies():
    """Some UVC cameras report 0x0 until the first successful grab."""
    source = _source(FakeCapture(frames=[_frame(320, 240)], size=(0, 0)))
    assert source.size == (0, 0)
    source.read()
    assert source.size == (320, 240)


def test_a_device_that_will_not_open_is_not_fatal():
    source = _source(FakeCapture(opened=False))
    assert not source.is_open
    assert source.read() is None
    assert "could not open" in source.last_error


def test_a_factory_that_raises_is_not_fatal():
    def boom(_device):
        raise OSError("no such device")

    source = CameraFrameSource("/dev/video99", capture_factory=boom)
    assert not source.is_open
    assert source.read() is None
    assert "no such device" in source.last_error


def test_a_dropped_frame_leaves_the_source_open():
    """A USB hiccup (or the thermal cam's NUC shutter) must not close us."""
    source = _source(FakeCapture(frames=[]))
    assert source.read() is None
    assert source.last_error == "no frame"
    assert source.is_open


def test_recovers_after_a_dropped_frame():
    source = _source(FakeCapture(frames=[None, _frame()]))
    source.read()
    # First entry is a falsy frame; the fake pops it, then the good one follows.
    assert source.read() is not None
    assert source.last_error is None


def test_close_releases_and_stops_reading():
    capture = FakeCapture(frames=[_frame(), _frame()])
    source = _source(capture)
    source.close()
    assert capture.released
    assert source.read() is None
    assert not source.is_open


def test_reopen_after_close():
    """Unplug/replug: open() gets a fresh capture rather than reusing the dead one."""
    source = CameraFrameSource(0, capture_factory=lambda _device: FakeCapture())
    source.close()
    assert source.open()
    assert source.read() is not None


def test_scope_source_falls_back_when_no_device_configured():
    from benchhud.shell.main_window import scope_source_for

    source = scope_source_for("  ")
    assert source.read() is not None
    assert source.status.reason == "no camera selected"


def test_scope_source_falls_back_when_the_camera_will_not_open():
    """Unplugged or busy scope must not leave the shell without a preview — or hide it."""
    from benchhud.shell.main_window import scope_source_for

    source = scope_source_for("/dev/video-nope")
    assert source.read() is not None
    assert not source.status.live
    assert source.status.reason.startswith("couldn't open /dev/video-nope")
    source.close()


def _cameras():
    from benchhud.devices.discovery import CameraDevice

    return [
        CameraDevice(identifier="0x8020000005ac8514", label="FaceTime HD", index=0),
        CameraDevice(identifier="0x1a11000005ac1112", label="Studio Display", index=1),
    ]


def test_qt_device_id_resolves_to_its_enumeration_index():
    """macOS/Windows ids are opaque to OpenCV — it needs the index."""
    from benchhud.devices.discovery import resolve_capture_device

    assert resolve_capture_device("0x1a11000005ac1112", _cameras()) == 1


def test_v4l2_path_is_passed_through():
    from benchhud.devices.discovery import resolve_capture_device

    assert resolve_capture_device("/dev/video4", []) == "/dev/video4"


def test_numeric_identifier_becomes_an_index():
    from benchhud.devices.discovery import resolve_capture_device

    assert resolve_capture_device("2", []) == 2


def test_unknown_identifier_is_left_alone():
    """Stale config: the open attempt fails and the caller falls back."""
    from benchhud.devices.discovery import resolve_capture_device

    assert resolve_capture_device("0xdeadbeef", _cameras()) == "0xdeadbeef"


def test_scope_source_opens_the_camera_at_the_resolved_index():
    from benchhud.shell.main_window import scope_source_for

    source = scope_source_for("0x1a11000005ac1112", _cameras())
    # No second camera on CI, so this falls back — the point is that resolution
    # happened: index 1 was tried rather than the id being opened verbatim.
    assert source._camera.device == 1
    source.close()


def test_status_names_the_camera_not_its_opaque_id():
    from benchhud.shell.main_window import camera_label

    assert camera_label("0x1a11000005ac1112", _cameras()) == "Studio Display"
    assert camera_label("/dev/video4", _cameras()) == "/dev/video4"


def test_open_hint_speaks_each_platform():
    from benchhud.shell.main_window import camera_open_hint

    assert "Privacy & Security" in camera_open_hint("darwin")
    assert "/dev/video" in camera_open_hint("linux")
    assert camera_open_hint("win32")
