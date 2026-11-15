"""Reduced-scale MJPEG decode for the live path, full size on snapshot (#349)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from benchhud.capture.camera import CameraFrameSource, full_size_of, live_reduction
from benchhud.capture.fallback import FallbackFrameSource
from benchhud.capture.frame_source import SyntheticFrameSource
from benchhud.config import Config
from benchhud.output.sink import FakeSink
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.main_window import MainWindow

FOURCC, FORMAT, CONVERT_RGB = 6, 8, 16
MJPG = 0x47504A4D
YUYV = 0x56595559


def _jpeg(width: int, height: int, value: int = 0) -> np.ndarray:
    """A camera-style compressed frame: one row of JPEG bytes."""
    image = np.full((height, width, 3), value, dtype=np.uint8)
    cv2.rectangle(image, (width // 4, height // 4), (width // 2, height // 2), (0, 200, 255), -1)
    ok, data = cv2.imencode(".jpg", image)
    assert ok
    return data.reshape(1, -1)


class MjpegCapture:
    """A fake camera that hands over compressed frames the way one backend does.

    ``raw_prop`` is the property that backend takes: ``FORMAT`` for FFmpeg,
    ``CONVERT_RGB`` for V4L2. ``lies`` accepts it and sends decoded frames anyway.
    """

    def __init__(
        self, size=(1920, 1080), *, fourcc=MJPG, raw_prop=FORMAT, lies=False, reported=None
    ) -> None:
        self.frame_size = size
        self.reported = reported or size
        self.fourcc = fourcc
        self.raw_prop = raw_prop
        self.lies = lies
        self.raw = False
        self.sets: list[tuple[int, float]] = []
        self.n = 0
        self.torn = False
        self.grabs = 0
        self.retrieves = 0

    def isOpened(self) -> bool:
        return True

    def get(self, prop: int) -> float:
        return float({3: self.reported[0], 4: self.reported[1], FOURCC: self.fourcc}.get(prop, 0))

    def set(self, prop: int, value: float) -> bool:
        self.sets.append((prop, value))
        if prop == self.raw_prop and value in (-1, 0):
            self.raw = True
            return True
        return False

    def grab(self) -> bool:
        self.grabs += 1
        self.n += 1
        return True

    def retrieve(self):
        self.retrieves += 1
        width, height = self.frame_size
        if self.raw and not self.lies:
            data = _jpeg(width, height, self.n % 200)
            return True, data[:, : data.size // 2] if self.torn else data
        return True, np.full((height, width, 3), self.n % 200, dtype=np.uint8)

    def read(self):
        self.grab()
        return self.retrieve()

    def release(self) -> None:
        pass


def _camera(capture, **kwargs) -> CameraFrameSource:
    return CameraFrameSource(0, reduced_live=True, capture_factory=lambda _d: capture, **kwargs)


@pytest.mark.parametrize(
    ("width", "factor"),
    [(3840, 4), (2560, 2), (1920, 2), (1280, 1), (640, 1), (0, 1), (7680, 8)],
)
def test_the_live_frame_is_never_narrower_than_960(width, factor) -> None:
    assert live_reduction(width) == factor


@pytest.mark.parametrize("raw_prop", [FORMAT, CONVERT_RGB])
def test_a_1080p_frame_is_live_at_half_size_and_snapshots_at_full(raw_prop) -> None:
    camera = _camera(MjpegCapture((1920, 1080), raw_prop=raw_prop))
    live = camera.read()
    assert live.shape == (540, 960, 3)
    assert camera.size == (1920, 1080)  # the camera's size, not the live frame's
    assert camera.snapshot().shape == (1080, 1920, 3)


def test_ffmpeg_is_not_also_asked_for_unconverted_frames() -> None:
    # It accepts CONVERT_RGB=0 too, then sends a grey plane instead of the JPEG.
    capture = MjpegCapture(raw_prop=FORMAT)
    _camera(capture)
    assert (FORMAT, -1) in capture.sets
    assert all(prop != CONVERT_RGB for prop, _ in capture.sets)


def test_a_4k_frame_is_live_at_a_quarter_and_snapshots_at_full() -> None:
    camera = _camera(MjpegCapture((3840, 2160)))
    assert camera.read().shape == (540, 960, 3)
    assert camera.snapshot().shape == (2160, 3840, 3)


def test_a_720p_frame_is_left_at_full_size() -> None:
    camera = _camera(MjpegCapture((1280, 720)))
    assert camera.read().shape == (720, 1280, 3)
    assert camera.snapshot().shape == (720, 1280, 3)


def test_the_snapshot_is_the_frame_on_screen() -> None:
    camera = _camera(MjpegCapture((1920, 1080)))
    camera.read()
    live = camera.read()
    full = camera.snapshot()
    # Same picture: the background level survives both decodes.
    assert abs(int(full[5, 5, 0]) - int(live[5, 5, 0])) <= 2
    assert int(live[5, 5, 0]) == pytest.approx(2, abs=2)


def test_a_camera_that_is_not_mjpeg_is_read_as_before() -> None:
    capture = MjpegCapture((1920, 1080), fourcc=YUYV)
    camera = _camera(capture)
    frame = camera.read()
    assert frame.shape == (1080, 1920, 3)
    assert capture.sets == []  # never asked for compressed frames
    assert camera.snapshot() is frame


def test_reduced_decode_is_off_unless_asked_for() -> None:
    capture = MjpegCapture((1920, 1080))
    camera = CameraFrameSource(0, capture_factory=lambda _d: capture)
    assert camera.read().shape == (1080, 1920, 3)
    assert capture.sets == []


def test_a_backend_that_sends_decoded_frames_anyway_falls_back_for_good() -> None:
    opened: list[MjpegCapture] = []

    def factory(_device):
        opened.append(MjpegCapture((1920, 1080), lies=True))
        return opened[-1]

    camera = CameraFrameSource(0, reduced_live=True, capture_factory=factory)
    assert camera.read() is None  # the frame that showed it wasn't a JPEG
    assert camera.is_open and len(opened) == 2  # reopened, without the request
    assert opened[1].sets == []
    frame = camera.read()
    assert frame.shape == (1080, 1920, 3)
    assert camera.snapshot() is frame


def test_a_torn_frame_is_a_missed_frame_not_a_lost_camera() -> None:
    capture = MjpegCapture((1920, 1080))
    camera = _camera(capture)
    good = camera.read()
    capture.torn = True
    torn = camera.read()
    # libjpeg may salvage half a picture or give up; either way it's no crash,
    # the camera stays open, and a later good frame decodes.
    assert torn is None or torn.shape == good.shape
    assert camera.is_open
    capture.torn = False
    assert camera.read().shape == (540, 960, 3)


def test_a_wrong_advertised_size_is_learned_from_the_frame() -> None:
    camera = _camera(MjpegCapture((1920, 1080), reported=(3840, 2160)))
    camera.read()
    assert camera.size == (1920, 1080)
    assert camera.read().shape == (540, 960, 3)


def test_skipped_frames_are_neither_fetched_nor_decoded() -> None:
    capture = MjpegCapture((1920, 1080))
    camera = _camera(capture)
    wanted = iter([False, False, True])
    camera.set_decode_gate(lambda: next(wanted))
    first = camera.read()
    assert camera.read() is first and camera.read() is first
    fresh = camera.read()
    assert fresh is not first and fresh.shape == (540, 960, 3)
    assert capture.grabs == 4 and capture.retrieves == 2 and camera.decodes == 2


def test_reopening_forgets_the_old_frame() -> None:
    camera = _camera(MjpegCapture((1920, 1080)))
    camera.read()
    camera.release()
    assert camera.snapshot() is None


# --- through the fallback and the shell ----------------------------------------


def test_the_fallback_passes_a_live_snapshot_through() -> None:
    source = FallbackFrameSource(
        _camera(MjpegCapture((1920, 1080))), SyntheticFrameSource(960, 540), sleep=lambda _s: None
    )
    assert source.snapshot() is None  # nothing read yet: not live
    live = source.read()
    assert live.shape == (540, 960, 3) and source.size == (1920, 1080)
    assert source.snapshot().shape == (1080, 1920, 3)
    assert full_size_of(live, source) == (1920, 1080)


def test_the_test_pattern_has_no_full_size_snapshot() -> None:
    source = FallbackFrameSource(None, SyntheticFrameSource(960, 540), sleep=lambda _s: None)
    frame = source.read()
    assert source.snapshot() is None
    assert full_size_of(frame, source) == (960, 540)


def test_a_frame_that_is_not_a_fraction_of_the_source_is_its_own_size() -> None:
    class Source:
        size = (1920, 1080)

    assert full_size_of(np.zeros((540, 960, 3), np.uint8), Source()) == (1920, 1080)
    assert full_size_of(np.zeros((270, 480, 3), np.uint8), Source()) == (1920, 1080)
    assert full_size_of(np.zeros((1080, 1920, 3), np.uint8), Source()) == (1920, 1080)
    assert full_size_of(np.zeros((480, 640, 3), np.uint8), Source()) == (640, 480)
    assert full_size_of(np.zeros((480, 640, 3), np.uint8), None) == (640, 480)


def _window(qtbot, sink_size=(1280, 720)) -> tuple[MainWindow, list]:
    config = Config()
    config.output.enabled = True
    config.output.width, config.output.height = sink_size
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.start_output(lambda d, w, h: FakeSink(w, h))
    shown: list = []
    window._show_composite = lambda frame, *a: shown.append(frame)
    source = FallbackFrameSource(
        _camera(MjpegCapture((1920, 1080))), SyntheticFrameSource(960, 540), sleep=lambda _s: None
    )
    window._scope_source = source
    window.scope_panel.update_frame(source.read())
    return window, shown


def test_the_shell_composes_the_live_frame_without_scaling_it_up(qtbot) -> None:
    window, shown = _window(qtbot)  # 720p out: more than the 960x540 live frame
    window.render_composite()
    assert shown[-1].shape == (540, 960, 3)
    assert window._compose_scale == pytest.approx(0.5)


def test_the_shell_still_shrinks_for_a_smaller_output(qtbot) -> None:
    window, shown = _window(qtbot, sink_size=(640, 360))
    window.render_composite()
    assert shown[-1].shape == (360, 640, 3)
    assert window._compose_scale == pytest.approx(1 / 3)


def test_the_registration_stays_in_the_cameras_pixels(qtbot) -> None:
    window, _shown = _window(qtbot)
    window.controls_panel.set_transform(
        RegistrationTransform(scale=3.85, offset_x=-300.0, offset_y=-600.0, rotation_deg=4.0)
    )
    window.thermal_panel.update_temps(np.full((192, 256), 25.0, dtype=np.float32))
    seen = {}

    def spy(scope, temps, transform, settings, marker=None):
        seen.update(shape=scope.shape, transform=transform)
        return scope

    import benchhud.shell.main_window as mw

    original, mw.compose = mw.compose, spy
    try:
        window.render_composite()
    finally:
        mw.compose = original
    assert seen["shape"] == (540, 960, 3)
    assert seen["transform"].offset_x == pytest.approx(-150.0)
    assert seen["transform"].offset_y == pytest.approx(-300.0)
    assert seen["transform"].scale == pytest.approx(3.85)


def test_capture_to_job_gets_the_full_size_frame(qtbot) -> None:
    window, _shown = _window(qtbot)
    assert window.scope_panel.last_frame.shape == (540, 960, 3)
    assert window.capture_context().scope.shape == (1080, 1920, 3)


def test_capture_to_job_without_a_camera_keeps_the_frame_it_has(qtbot) -> None:
    window, _shown = _window(qtbot)
    window._scope_source = None
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    window.scope_panel.update_frame(frame)
    assert window.capture_context().scope is frame
