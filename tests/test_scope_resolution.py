"""Picking the scope camera's resolution (#247)."""

from __future__ import annotations

from benchhud.capture.camera import CameraFrameSource
from benchhud.capture.modes import COMMON_SIZES, camera_modes, describe, parse_formats
from benchhud.config import Config
from benchhud.devices.discovery import CameraDevice
from benchhud.shell.preferences import PreferencesDialog

# The bench's microscope (YW3609A), trimmed.
BTER = """ioctl: VIDIOC_ENUM_FMT
\tType: Video Capture

\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 3840x2160
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t\tSize: Discrete 2560x1440
\t\tSize: Discrete 2560x1600
\t\tSize: Discrete 1920x1080
\t[1]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 640x480
\t\tSize: Discrete 320x240
"""


def test_mjpg_sizes_win_largest_first() -> None:
    assert parse_formats(BTER) == [(3840, 2160), (2560, 1600), (2560, 1440), (1920, 1080)]


def test_a_camera_without_mjpg_offers_what_it_has() -> None:
    yuyv = "\t[0]: 'YUYV' (YUYV 4:2:2)\n\t\tSize: Discrete 640x480\n\t\tSize: Discrete 320x240\n"
    assert parse_formats(yuyv) == [(640, 480), (320, 240)]
    assert parse_formats("") == []


def test_linux_asks_the_camera() -> None:
    calls = []

    def run(argv):
        calls.append(argv)
        return BTER

    sizes = camera_modes("/dev/video2", platform="linux", run=run, which=lambda _c: "/usr/bin/x")
    assert sizes[0] == (3840, 2160)
    assert calls == [["v4l2-ctl", "-d", "/dev/video2", "--list-formats-ext"]]


def test_elsewhere_or_without_v4l2_ctl_it_offers_common_sizes() -> None:
    assert camera_modes("0x1234", platform="darwin") == list(COMMON_SIZES)
    no_tool = camera_modes("/dev/video2", platform="linux", which=lambda _c: None)
    assert no_tool == list(COMMON_SIZES)


def test_descriptions() -> None:
    assert describe((0, 0)) == "Camera default"
    assert describe((1920, 1080)) == "1920 x 1080"
    assert "heavier on the CPU" in describe((3840, 2160))


class FakeCapture:
    def __init__(self, gives=(1920, 1080)) -> None:
        self.props = {3: 640.0, 4: 480.0}
        self.gives = gives
        self.sets: list[tuple[int, float]] = []

    def isOpened(self):
        return True

    def read(self):
        return False, None

    def get(self, prop):
        return self.props.get(prop, 0.0)

    def set(self, prop, value):
        self.sets.append((prop, value))
        if prop in (3, 4):  # the camera takes what it can
            self.props[prop] = float(self.gives[prop - 3])
        return True

    def release(self):
        pass


def test_a_chosen_size_asks_for_mjpg_at_that_size() -> None:
    capture = FakeCapture()
    camera = CameraFrameSource("/dev/video2", size=(1920, 1080), capture_factory=lambda _d: capture)
    assert capture.sets[0] == (6, 0x47504A4D)  # MJPG first
    assert (3, 1920) in capture.sets and (4, 1080) in capture.sets
    assert camera.size == (1920, 1080) and camera.size_note is None


def test_the_camera_saying_no_is_reported() -> None:
    capture = FakeCapture(gives=(1280, 720))
    camera = CameraFrameSource("/dev/video2", size=(3840, 2160), capture_factory=lambda _d: capture)
    assert camera.size_note == "asked for 3840x2160, the camera gave 1280x720"


def test_camera_default_asks_for_nothing() -> None:
    capture = FakeCapture()
    camera = CameraFrameSource("/dev/video2", capture_factory=lambda _d: capture)
    assert capture.sets == [] and camera.size == (640, 480)


class Scanner:
    def cameras(self):
        return [CameraDevice(identifier="/dev/video2", label="YW3609A", index=2)]

    def serial_ports(self):
        return []


def test_preferences_offer_the_cameras_sizes_and_save_the_choice(qtbot) -> None:
    config = Config()
    config.panels.scope.device = "/dev/video2"
    dialog = PreferencesDialog(config, Scanner(), camera_modes=lambda _d: parse_formats(BTER))
    qtbot.addWidget(dialog)
    combo = dialog.scope_resolution
    assert combo.itemText(0) == "Camera default" and combo.currentIndex() == 0
    assert "3840 x 2160" in combo.itemText(1)
    combo.setCurrentIndex(combo.findData("1920x1080"))
    dialog.apply_to_config()
    assert (config.panels.scope.width, config.panels.scope.height) == (1920, 1080)


def test_a_configured_size_the_camera_doesnt_list_is_kept(qtbot) -> None:
    config = Config()
    config.panels.scope.device = "/dev/video2"
    config.panels.scope.width, config.panels.scope.height = 1600, 900
    dialog = PreferencesDialog(config, Scanner(), camera_modes=lambda _d: parse_formats(BTER))
    qtbot.addWidget(dialog)
    assert dialog.scope_size == (1600, 900)
    assert "(configured)" in dialog.scope_resolution.currentText()
