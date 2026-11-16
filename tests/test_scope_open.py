"""How the scope camera is opened (#377), with a stand-in for cv2 and no camera.

On the bench OpenCV 5 opens ``/dev/video4`` with FFmpeg, which refuses the size
and format requests; named V4L2, the same camera takes them. ``FakeCv2`` models
both backends the way they were measured there.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import ClassVar

import numpy as np
import pytest

from benchhud.capture import camera as camera_module
from benchhud.capture.camera import CameraFrameSource, fourcc_text, open_arguments

WIDTH, HEIGHT, FOURCC, FORMAT, CONVERT_RGB = 3, 4, 6, 8, 16
CAP_V4L2 = 200
MJPG = 0x47504A4D
YUYV = int.from_bytes(b"YUYV", "little")
BY_ID = "/dev/v4l/by-id/usb-Elgato_Cam_Link_4K-video-index0"


class FakeVideoCapture:
    """One camera left in 4K MJPG, seen through FFmpeg or V4L2."""

    opened: ClassVar[list[tuple]] = []
    #: What the V4L2 camera gives whatever it is asked for; None takes the request.
    only: ClassVar[tuple[int, int] | None] = None

    def __init__(self, *args) -> None:
        type(self).opened.append(args)
        self.backend = "V4L2" if args[1:] == (CAP_V4L2,) else "FFMPEG"
        self.props = {WIDTH: 3840.0, HEIGHT: 2160.0, FOURCC: float(MJPG)}
        self.raw = False

    def isOpened(self) -> bool:
        return True

    def getBackendName(self) -> str:
        return self.backend

    def get(self, prop: int) -> float:
        return self.props.get(prop, 0.0)

    def set(self, prop: int, value: float) -> bool:
        if self.backend == "FFMPEG":
            if prop == FORMAT and value == -1:
                self.raw = True
                return True
            return False  # the fourcc and the size: refused, as measured
        if prop == CONVERT_RGB and value == 0:
            self.raw = True
            return True
        if prop in (WIDTH, HEIGHT):
            given = type(self).only
            self.props[prop] = float(given[prop - WIDTH]) if given else float(value)
            return True
        if prop == FOURCC:
            self.props[prop] = float(value)
            return True
        return False

    def read(self):
        width, height = int(self.props[WIDTH]), int(self.props[HEIGHT])
        frame = np.full((height, width, 3), 90, dtype=np.uint8)
        if not self.raw:
            return True, frame
        import cv2  # the real one: the fake only stands in during the open

        ok, data = cv2.imencode(".jpg", frame)
        assert ok
        return True, data.reshape(1, -1)

    def release(self) -> None:
        pass


@pytest.fixture
def fake_cv2(monkeypatch):
    """Swap cv2 for the fake while a camera opens; ``.restore()`` brings it back."""
    FakeVideoCapture.opened = []
    FakeVideoCapture.only = None
    patch = pytest.MonkeyPatch()
    patch.setitem(sys.modules, "cv2", types.SimpleNamespace(VideoCapture=FakeVideoCapture))
    FakeVideoCapture.restore = patch.undo
    yield FakeVideoCapture
    patch.undo()


def _open(monkeypatch, fake_cv2, platform, device, **kwargs) -> CameraFrameSource:
    monkeypatch.setattr(camera_module.sys, "platform", platform)
    camera = CameraFrameSource(device, **kwargs)
    fake_cv2.restore()  # reads decode with the real cv2
    return camera


@pytest.mark.parametrize(
    ("platform", "device", "expected"),
    [
        ("linux", "/dev/video4", ("/dev/video4", CAP_V4L2)),
        ("linux", BY_ID, (BY_ID, CAP_V4L2)),
        ("linux", 0, (0,)),  # an index is OpenCV's to place
        ("linux", "rtsp://bench/scope", ("rtsp://bench/scope",)),  # not a V4L2 device
        ("darwin", 0, (0,)),
        ("darwin", "/dev/video4", ("/dev/video4",)),
        ("win32", 1, (1,)),
    ],
)
def test_only_a_linux_device_path_names_the_v4l2_backend(platform, device, expected) -> None:
    assert open_arguments(device, platform) == expected


def test_the_platform_defaults_to_the_one_running(monkeypatch) -> None:
    monkeypatch.setattr(camera_module.sys, "platform", "linux")
    assert open_arguments("/dev/video4") == ("/dev/video4", CAP_V4L2)
    monkeypatch.setattr(camera_module.sys, "platform", "darwin")
    assert open_arguments("/dev/video4") == ("/dev/video4",)


@pytest.mark.parametrize("device", ["/dev/video4", BY_ID])
def test_on_linux_the_requested_size_reaches_the_camera(monkeypatch, fake_cv2, device) -> None:
    camera = _open(monkeypatch, fake_cv2, "linux", device, size=(1920, 1080))
    assert fake_cv2.opened == [(device, CAP_V4L2)]
    assert camera.size == (1920, 1080) and camera.size_note is None
    assert camera.read().shape == (1080, 1920, 3)
    assert camera.size_note is None


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_macos_and_windows_open_the_camera_as_before(monkeypatch, fake_cv2, platform) -> None:
    _open(monkeypatch, fake_cv2, platform, "1", size=(1920, 1080))
    assert fake_cv2.opened == [(1,)]


def test_a_refused_size_is_noted_and_logged(monkeypatch, fake_cv2, caplog) -> None:
    """What OpenCV's own backend choice did on the bench, kept as the check's test."""
    with caplog.at_level(logging.INFO, logger="benchhud.capture.camera"):
        camera = _open(monkeypatch, fake_cv2, "darwin", "/dev/video4", size=(1920, 1080))
    assert camera.size == (3840, 2160)
    assert camera.size_note == "asked for 1920x1080, the camera gave 3840x2160"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == [
        "camera /dev/video4: asked for 1920x1080, the camera gave 3840x2160"
    ]
    assert "opened: 3840x2160 MJPG, backend FFMPEG" in caplog.text


def test_a_camera_without_the_size_is_noted_on_linux_too(monkeypatch, fake_cv2, caplog) -> None:
    fake_cv2.only = (1280, 720)
    with caplog.at_level(logging.WARNING, logger="benchhud.capture.camera"):
        camera = _open(monkeypatch, fake_cv2, "linux", "/dev/video4", size=(1920, 1080))
    assert camera.size_note == "asked for 1920x1080, the camera gave 1280x720"
    assert "asked for 1920x1080, the camera gave 1280x720" in caplog.text


def test_the_frame_decides_what_the_camera_delivered(caplog) -> None:
    """A driver that says yes and sends something else is caught on the first frame."""

    class Liar:
        def isOpened(self):
            return True

        def get(self, prop):
            return {WIDTH: 1920.0, HEIGHT: 1080.0}.get(prop, 0.0)

        def set(self, prop, value):
            return True

        def read(self):
            return True, np.zeros((720, 1280, 3), dtype=np.uint8)

        def release(self):
            pass

    with caplog.at_level(logging.WARNING, logger="benchhud.capture.camera"):
        camera = CameraFrameSource(
            "/dev/video4", size=(1920, 1080), capture_factory=lambda _d: Liar()
        )
        assert camera.size_note is None
        camera.read()
        camera.read()
    assert camera.size == (1280, 720)
    assert camera.size_note == "asked for 1920x1080, the camera gave 1280x720"
    assert caplog.text.count("the camera gave 1280x720") == 1  # once, not per frame


def test_a_driver_that_does_not_know_its_size_yet_is_not_a_refusal(caplog) -> None:
    class Late:
        def isOpened(self):
            return True

        def get(self, prop):
            return 0.0

        def set(self, prop, value):
            return True

        def read(self):
            return True, np.zeros((1080, 1920, 3), dtype=np.uint8)

        def release(self):
            pass

    with caplog.at_level(logging.WARNING, logger="benchhud.capture.camera"):
        camera = CameraFrameSource(
            "/dev/video4", size=(1920, 1080), capture_factory=lambda _d: Late()
        )
        assert camera.size_note is None
        camera.read()
    assert camera.size == (1920, 1080) and camera.size_note is None
    assert caplog.records == []


@pytest.mark.parametrize("reduced_live", [True, False])
def test_reduced_live_works_both_ways_through_v4l2(monkeypatch, fake_cv2, reduced_live) -> None:
    camera = _open(
        monkeypatch, fake_cv2, "linux", BY_ID, size=(1920, 1080), reduced_live=reduced_live
    )
    live = camera.read()
    assert live.shape == ((540, 960, 3) if reduced_live else (1080, 1920, 3))
    assert camera.size == (1920, 1080) and camera.size_note is None
    assert camera.snapshot().shape == (1080, 1920, 3)  # capture to job stays full size


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (MJPG, "MJPG"),
        (YUYV, "YUYV"),
        (int.from_bytes(b"Y16 ", "little"), "Y16"),
        (0, "?"),
        (-1, "?"),
    ],
)
def test_fourcc_text(value, text) -> None:
    assert fourcc_text(float(value)) == text
