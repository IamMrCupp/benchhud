"""The Cameras panel (#330): sliders from what the camera reports, saved shots."""

from __future__ import annotations

import errno

from benchhud.config import CameraConfig
from benchhud.devices.camera_controls import (
    PAN_ABSOLUTE,
    TILT_ABSOLUTE,
    ZOOM_ABSOLUTE,
    CameraControls,
)
from benchhud.devices.discovery import CameraDevice
from benchhud.shell.panels.cameras import CamerasPanel, describe
from test_camera_controls import PIXY, FakeCamera


class FakeScanner:
    def __init__(self, cameras: list[CameraDevice]) -> None:
        self._cameras = cameras

    def cameras(self) -> list[CameraDevice]:
        return self._cameras

    def serial_ports(self) -> list:
        return []


def _panel(
    qtbot, cameras, fakes, *, texts=(), items=(), answers=(), scanner=None, platform="linux"
):
    texts, items, answers = list(texts), list(items), list(answers)

    def factory(device: str) -> CameraControls:
        return fakes[device].controls_for(device)

    panel = CamerasPanel(
        cameras,
        scanner=scanner,
        controls_factory=factory,
        ask_text=lambda *_: texts.pop(0) if texts else None,
        ask_item=lambda *_: items.pop(0) if items else None,
        confirm=lambda *_: answers.pop(0) if answers else False,
        platform=platform,
    )
    qtbot.addWidget(panel)
    return panel


def test_sliders_follow_the_camera(qtbot) -> None:
    pixy = FakeCamera(PIXY)
    pixy.values[PAN_ABSOLUTE] = 36000
    panel = _panel(qtbot, [CameraConfig("PIXY", "/dev/pixy")], {"/dev/pixy": pixy})

    pan = panel.sliders["pan"]
    assert (pan.minimum(), pan.maximum(), pan.value()) == (-540000, 540000, 36000)
    assert panel.values["pan"].text() == "+10.0°"
    assert panel.values["zoom"].text() == "1.00x"
    assert not panel.sliders["pan"].isHidden()


def test_axes_a_camera_lacks_are_hidden(qtbot) -> None:
    scope = FakeCamera({ZOOM_ABSOLUTE: (100, 500, 1, 100, 0)})
    panel = _panel(qtbot, [CameraConfig("Scope", "/dev/scope")], {"/dev/scope": scope})

    assert panel.sliders["pan"].isHidden()
    assert panel.sliders["tilt"].isHidden()
    assert not panel.sliders["zoom"].isHidden()


def test_a_camera_with_no_ptz_says_so(qtbot) -> None:
    panel = _panel(qtbot, [CameraConfig("Thermal", "/dev/t")], {"/dev/t": FakeCamera({})})

    assert "no pan, tilt or zoom" in panel.status.text()
    assert all(s.isHidden() for s in panel.sliders.values())
    assert not panel.save_button.isEnabled()


def test_moving_a_slider_moves_the_camera(qtbot) -> None:
    pixy = FakeCamera(PIXY)
    panel = _panel(qtbot, [CameraConfig("PIXY", "/dev/pixy")], {"/dev/pixy": pixy})

    panel.sliders["tilt"].setValue(-36000)  # a click or a key: moves at once

    assert pixy.values[TILT_ABSOLUTE] == -36000
    assert panel.values["tilt"].text() == "-10.0°"


def test_dragging_moves_only_on_release(qtbot) -> None:
    pixy = FakeCamera(PIXY)
    panel = _panel(qtbot, [CameraConfig("PIXY", "/dev/pixy")], {"/dev/pixy": pixy})
    slider = panel.sliders["pan"]

    slider.setSliderDown(True)
    for value in (3600, 7200, 10800):
        slider.setValue(value)
    assert pixy.writes == []
    slider.setSliderDown(False)  # emits sliderReleased

    assert pixy.writes == [(PAN_ABSOLUTE, 10800)]


def test_save_and_go_to_a_shot(qtbot) -> None:
    pixy = FakeCamera(PIXY)
    cams = [CameraConfig("PIXY", "/dev/pixy")]
    panel = _panel(qtbot, cams, {"/dev/pixy": pixy}, texts=["Reflow"])
    panel.sliders["pan"].setValue(108000)
    panel.sliders["zoom"].setValue(150)

    panel.save_shot()
    assert cams[0].shots == {"Reflow": {"zoom": 150, "pan": 108000, "tilt": 0}}
    assert panel.shot_combo.currentText() == "Reflow"

    panel.sliders["pan"].setValue(0)
    panel.sliders["zoom"].setValue(100)
    pixy.writes.clear()
    assert panel.go_to_shot("Reflow")

    assert pixy.values[PAN_ABSOLUTE] == 108000
    assert pixy.values[ZOOM_ABSOLUTE] == 150
    assert panel.sliders["pan"].value() == 108000


def test_saving_over_a_shot_asks_first(qtbot) -> None:
    cams = [CameraConfig("PIXY", "/dev/pixy", {"Face": {"pan": 0}})]
    panel = _panel(qtbot, cams, {"/dev/pixy": FakeCamera(PIXY)}, texts=["Face"], answers=[False])
    panel.sliders["pan"].setValue(3600)

    panel.save_shot()

    assert cams[0].shots == {"Face": {"pan": 0}}


def test_a_shot_moves_what_the_camera_has(qtbot) -> None:
    scope = FakeCamera({ZOOM_ABSOLUTE: (100, 500, 1, 100, 0)})
    cams = [CameraConfig("Scope", "/dev/scope", {"Close": {"zoom": 300, "pan": 3600}})]
    panel = _panel(qtbot, cams, {"/dev/scope": scope})

    assert panel.go_to_shot("Close")

    assert scope.writes == [(ZOOM_ABSOLUTE, 300)]
    assert "no pan" in panel.status.text()


def test_delete_shot_asks(qtbot) -> None:
    cams = [CameraConfig("PIXY", "/dev/pixy", {"Face": {"pan": 0}, "Desk": {"tilt": -36000}})]
    panel = _panel(qtbot, cams, {"/dev/pixy": FakeCamera(PIXY)}, answers=[False, True])
    panel.shot_combo.setCurrentText("Desk")

    panel.delete_shot()
    assert "Desk" in cams[0].shots
    panel.delete_shot()
    assert list(cams[0].shots) == ["Face"]


def test_unreachable_camera_reports_and_disables(qtbot) -> None:
    def gone(path: str, flags: int) -> int:
        raise FileNotFoundError(errno.ENOENT, "No such file or directory")

    cams = [CameraConfig("PIXY", "/dev/pixy", {"Face": {"pan": 0}})]
    panel = CamerasPanel(
        cams,
        controls_factory=lambda d: CameraControls(d, ioctl=lambda *_: b"", opener=gone),
        platform="linux",
    )
    qtbot.addWidget(panel)

    assert "Can't reach PIXY" in panel.status.text()
    assert not panel.go_button.isEnabled()
    assert not panel.go_to_shot("Face")


def test_add_camera_from_the_scanner(qtbot) -> None:
    cams: list[CameraConfig] = []
    scanner = FakeScanner(
        [
            CameraDevice("/dev/v4l/by-id/usb-EMEET_PIXY-video-index0", "EMEET PIXY", 0),
        ]
    )
    pixy = FakeCamera(PIXY)
    panel = _panel(
        qtbot,
        cams,
        {"/dev/v4l/by-id/usb-EMEET_PIXY-video-index0": pixy},
        scanner=scanner,
        items=["EMEET PIXY (/dev/v4l/by-id/usb-EMEET_PIXY-video-index0)"],
        texts=["PIXY"],
    )

    panel.add_camera()

    assert cams == [CameraConfig("PIXY", "/dev/v4l/by-id/usb-EMEET_PIXY-video-index0")]
    assert panel.camera_combo.currentText() == "PIXY"
    assert not panel.sliders["pan"].isHidden()


def test_remove_camera_asks(qtbot) -> None:
    cams = [CameraConfig("PIXY", "/dev/pixy")]
    panel = _panel(qtbot, cams, {"/dev/pixy": FakeCamera(PIXY)}, answers=[True])

    panel.remove_camera()

    assert cams == []
    assert panel.camera_combo.count() == 0


def test_not_linux_touches_no_camera(qtbot) -> None:
    def never(device: str) -> CameraControls:
        raise AssertionError("opened a camera off Linux")

    panel = CamerasPanel(
        [CameraConfig("PIXY", "/dev/pixy")], controls_factory=never, platform="darwin"
    )
    qtbot.addWidget(panel)

    assert "needs Linux" in panel.status.text()
    assert not panel.add_button.isEnabled()
    assert not panel.go_button.isEnabled()


def test_describe() -> None:
    from benchhud.devices.camera_controls import ControlRange

    assert describe("pan", -54000, ControlRange(-540000, 540000, 3600, 0)) == "-15.0°"
    assert describe("zoom", 125, ControlRange(100, 150, 1, 100)) == "1.25x"
    assert describe("zoom", 3, ControlRange(0, 10, 1, 0)) == "3"
