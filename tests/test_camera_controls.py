"""Pan/tilt/zoom over V4L2 controls (#330), against a fake camera's ioctls."""

from __future__ import annotations

import errno
import os
import struct
import tomllib

import pytest
import tomlkit

from benchhud.config import CameraConfig, Config
from benchhud.devices.camera_controls import (
    PAN_ABSOLUTE,
    TILT_ABSOLUTE,
    ZOOM_ABSOLUTE,
    CameraControls,
    ControlRange,
)

_QUERY = struct.Struct("<II32siiiiI2I")
_CTRL = struct.Struct("<Ii")


class FakeCamera:
    """Answers QUERYCTRL / G_CTRL / S_CTRL like uvcvideo would."""

    def __init__(self, controls: dict[int, tuple[int, int, int, int, int]]) -> None:
        # id -> (min, max, step, default, flags)
        self.controls = controls
        self.values = {cid: spec[3] for cid, spec in controls.items()}
        self.writes: list[tuple[int, int]] = []
        self.opened: list[tuple[str, int]] = []
        self.open_fds = 0

    def open(self, path: str, flags: int) -> int:
        self.opened.append((path, flags))
        self.open_fds += 1
        return 3

    def close(self, fd: int) -> None:
        self.open_fds -= 1

    def ioctl(self, fd: int, request: int, payload: bytes) -> bytes:
        if request == 0xC0445624:  # QUERYCTRL
            cid = _QUERY.unpack(payload)[0]
            if cid not in self.controls:
                raise OSError(errno.EINVAL, "Invalid argument")
            lo, hi, step, default, flags = self.controls[cid]
            return _QUERY.pack(cid, 1, b"x", lo, hi, step, default, flags, 0, 0)
        cid, value = _CTRL.unpack(payload)
        if request == 0xC008561B:  # G_CTRL
            return _CTRL.pack(cid, self.values[cid])
        if request == 0xC008561C:  # S_CTRL
            self.values[cid] = value
            self.writes.append((cid, value))
            return payload
        raise AssertionError(f"unexpected ioctl {request:#x}")

    def controls_for(self, device: str = "/dev/v4l/by-id/pixy") -> CameraControls:
        return CameraControls(device, ioctl=self.ioctl, opener=self.open, closer=self.close)


PIXY = {
    PAN_ABSOLUTE: (-540000, 540000, 3600, 0, 0),
    TILT_ABSOLUTE: (-324000, 324000, 3600, 0, 0),
    ZOOM_ABSOLUTE: (100, 150, 1, 100, 0),
}


def test_reads_the_axes_the_camera_offers() -> None:
    cam = FakeCamera(PIXY)

    axes = cam.controls_for().axes()

    assert axes == {
        "zoom": ControlRange(100, 150, 1, 100),
        "pan": ControlRange(-540000, 540000, 3600, 0),
        "tilt": ControlRange(-324000, 324000, 3600, 0),
    }


def test_a_camera_without_ptz_offers_nothing() -> None:
    assert FakeCamera({}).controls_for().axes() == {}


def test_zoom_only_camera() -> None:
    cam = FakeCamera({ZOOM_ABSOLUTE: (100, 500, 1, 100, 0)})

    assert set(cam.controls_for().axes()) == {"zoom"}


@pytest.mark.parametrize("flags", [0x0001, 0x0004])  # disabled, read-only
def test_unusable_controls_are_skipped(flags: int) -> None:
    cam = FakeCamera({PAN_ABSOLUTE: (-36000, 36000, 3600, 0, flags)})

    assert cam.controls_for().axes() == {}


def test_an_inactive_control_is_still_offered() -> None:
    # Pan may go inactive while the camera tracks; it's back once tracking stops.
    cam = FakeCamera({PAN_ABSOLUTE: (-36000, 36000, 3600, 0, 0x0010)})

    assert set(cam.controls_for().axes()) == {"pan"}


def test_moves_are_absolute_clamped_and_on_step() -> None:
    cam = FakeCamera(PIXY)
    controls = cam.controls_for()
    axes = controls.axes()

    written = controls.move_to({"pan": 10_000, "tilt": -999_999, "zoom": 140}, axes)

    assert written == {"zoom": 140, "pan": 10_800, "tilt": -324000}
    # zoom first, so the pan lands on the framing the shot was saved with
    assert [cid for cid, _ in cam.writes] == [ZOOM_ABSOLUTE, PAN_ABSOLUTE, TILT_ABSOLUTE]


def test_skips_axes_the_camera_lacks() -> None:
    cam = FakeCamera({ZOOM_ABSOLUTE: (100, 500, 1, 100, 0)})
    controls = cam.controls_for()

    written = controls.move_to({"pan": 3600, "zoom": 200}, controls.axes())

    assert written == {"zoom": 200}
    assert cam.writes == [(ZOOM_ABSOLUTE, 200)]


def test_reads_positions() -> None:
    cam = FakeCamera(PIXY)
    cam.values[PAN_ABSOLUTE] = 72000
    controls = cam.controls_for()

    assert controls.positions(controls.axes())["pan"] == 72000


def test_opens_read_write_and_holds_nothing_open() -> None:
    cam = FakeCamera(PIXY)
    controls = cam.controls_for("/dev/v4l/by-id/pixy")

    controls.move_to({"pan": 3600}, controls.axes())

    assert cam.open_fds == 0
    assert all(path == "/dev/v4l/by-id/pixy" for path, _ in cam.opened)
    assert all(flags & os.O_RDWR for _, flags in cam.opened)


def test_a_missing_device_raises() -> None:
    def gone(path: str, flags: int) -> int:
        raise FileNotFoundError(errno.ENOENT, "No such file or directory")

    controls = CameraControls("/dev/v4l/by-id/gone", ioctl=lambda *_: b"", opener=gone)

    with pytest.raises(OSError):
        controls.axes()


# --- config ----------------------------------------------------------------


def test_cameras_and_shots_round_trip_through_toml() -> None:
    cfg = Config()
    cfg.cameras = [
        CameraConfig("PIXY", "/dev/v4l/by-id/pixy", {"Face": {"pan": 0, "tilt": 0, "zoom": 100}}),
        CameraConfig("C922", "/dev/v4l/by-id/c922"),
    ]

    loaded = Config.from_dict(tomllib.loads(tomlkit.dumps(cfg.to_dict())))

    assert loaded.cameras == cfg.cameras


def test_bad_camera_entries_are_dropped_not_fatal() -> None:
    data = {
        "cameras": [
            {
                "name": "PIXY",
                "device": "/dev/v4l/by-id/pixy",
                "shots": {
                    "Face": {"pan": 3600, "tilt": "up", "zoom": True, "roll": 5},
                    "Empty": {"focus": 3},
                    "Junk": 7,
                },
            },
            {"name": "", "device": "/dev/video0"},
            {"device": "/dev/video2"},
            "nonsense",
        ]
    }

    cfg = Config.from_dict(data)

    assert cfg.cameras == [CameraConfig("PIXY", "/dev/v4l/by-id/pixy", {"Face": {"pan": 3600}})]


def test_no_cameras_by_default() -> None:
    assert Config().cameras == []
    assert Config.from_dict({"cameras": "PIXY"}).cameras == []
