"""The scope-only and thermal-only feeds beside the composite (#344)."""

from __future__ import annotations

import numpy as np

from benchhud.config import Config
from benchhud.output.feeds import fit_frame, thermal_frame, thermal_size
from benchhud.output.sink import FakeSink, NullSink
from benchhud.shell.main_window import MainWindow


def _config(scope: str = "/dev/video12", thermal: str = "/dev/video13") -> Config:
    config = Config()
    config.output.enabled = True
    config.output.device = "/dev/video10"
    config.output.width, config.output.height = 128, 72
    config.output.scope_device = scope
    config.output.thermal_device = thermal
    return config


def _window(qtbot, config: Config, *, linux: bool = True) -> tuple[MainWindow, dict]:
    window = MainWindow(config)
    qtbot.addWidget(window)
    window._linux_output = linux
    sinks: dict[str, FakeSink] = {}

    def factory(device: str, width: int, height: int) -> FakeSink:
        sinks[device] = FakeSink(width, height)
        return sinks[device]

    window.start_output(factory)
    return window, sinks


def _scope() -> np.ndarray:
    frame = np.zeros((108, 192, 3), dtype=np.uint8)
    frame[:, :96] = (0, 0, 255)  # left half red: proves it isn't stretched or cropped
    return frame


def _temps() -> np.ndarray:
    temps = np.full((192, 256), 25.0, dtype=np.float32)
    temps[90:100, 120:130] = 300.0
    return temps


def test_feed_devices_survive_a_save_and_load(tmp_path) -> None:
    from benchhud.config import load, save

    path = tmp_path / "config.toml"
    save(_config(), path)
    loaded = load(path)
    assert loaded.output.scope_device == "/dev/video12"
    assert loaded.output.thermal_device == "/dev/video13"


def test_thermal_feed_takes_the_cameras_shape_at_the_output_height() -> None:
    assert thermal_size(720) == (960, 720)  # landscape 256x192
    assert thermal_size(720, (256, 192)) == (540, 720)  # the bench's portrait camera
    assert thermal_size(73) == (96, 72)  # even sides for the loopback


def test_fit_keeps_the_aspect_and_letterboxes() -> None:
    wide = np.full((90, 160, 3), 200, dtype=np.uint8)
    out = fit_frame(wide, (160, 120))
    assert out.shape == (120, 160, 3)
    assert out[0].max() == 0 and out[-1].max() == 0  # bars top and bottom
    assert out[60, 80, 0] == 200
    same = np.zeros((72, 128, 3), dtype=np.uint8)
    assert fit_frame(same, (128, 72)) is same  # already the right size: untouched


def test_thermal_frame_is_colourised_at_the_feed_size() -> None:
    import cv2

    inferno = cv2.COLORMAP_INFERNO
    out = thermal_frame(_temps(), 20.0, 100.0, inferno, (960, 720), scale_bar=False)
    assert out.shape == (720, 960, 3)
    top = cv2.applyColorMap(np.array([[255]], dtype=np.uint8), inferno)[0, 0]
    assert (out[356, 468] == top).all()  # the 300 °C spot, scaled 3.75x, is the hottest colour
    assert not (out[50, 50] == top).all()


def test_scope_feed_opens_beside_the_composite_and_thermal_on_its_first_frame(qtbot) -> None:
    window, sinks = _window(qtbot, _config())
    assert set(sinks) == {"/dev/video10", "/dev/video12"}
    assert sinks["/dev/video12"].size == (128, 72)
    portrait = np.full((256, 192), 25.0, dtype=np.float32)
    window.thermal_panel.update_temps(portrait)
    window.render_composite()
    assert sinks["/dev/video13"].size == (54, 72)  # the camera's portrait shape


def test_blank_devices_open_no_feeds(qtbot) -> None:
    _w, sinks = _window(qtbot, _config(scope="", thermal="  "))
    assert set(sinks) == {"/dev/video10"}


def test_no_extra_feeds_off_linux(qtbot) -> None:
    window, sinks = _window(qtbot, _config(), linux=False)
    window.thermal_panel.update_temps(_temps())
    window.render_composite()
    assert set(sinks) == {"/dev/video10"}


def test_each_feed_gets_its_own_picture(qtbot) -> None:
    window, sinks = _window(qtbot, _config())
    window.thermal_panel.update_temps(_temps())
    window.scope_panel.update_frame(_scope())
    window.render_composite()

    scope = sinks["/dev/video12"].frames[-1]
    assert scope.shape == (72, 128, 3)
    assert scope[36, 10, 2] > 200 and scope[36, 118, 2] < 50  # red left, not right: unstretched
    thermal = sinks["/dev/video13"].frames[-1]
    assert thermal.shape == (72, 96, 3)
    assert sinks["/dev/video10"].frame_count >= 1  # the composite still goes out


def test_thermal_feed_runs_without_a_scope(qtbot) -> None:
    window, sinks = _window(qtbot, _config())
    window.thermal_panel.update_temps(_temps())
    window.render_composite()
    assert sinks["/dev/video13"].frame_count == 1
    assert sinks["/dev/video12"].frame_count == 0


def test_stop_closes_every_feed(qtbot) -> None:
    window, sinks = _window(qtbot, _config())
    window.thermal_panel.update_temps(_temps())
    window.render_composite()
    window.stop_output()
    assert len(sinks) == 3
    assert all(sink.closed for sink in sinks.values())


def test_a_dying_feed_is_dropped_without_touching_the_composite(qtbot) -> None:
    window, sinks = _window(qtbot, _config())

    def boom(_frame):
        raise OSError("device went away")

    sinks["/dev/video12"].send = boom
    window.scope_panel.update_frame(_scope())
    window.render_composite()
    window.render_composite()
    assert sinks["/dev/video12"].closed
    assert sinks["/dev/video10"].frame_count == 2


def test_a_feed_that_wont_open_is_named_in_the_warning(qtbot) -> None:
    window = MainWindow(_config())
    qtbot.addWidget(window)
    window._linux_output = True

    def factory(device, width, height):
        if device == "/dev/video13":
            return NullSink(width, height, reason="/dev/video13 does not exist")
        return FakeSink(width, height)

    window.start_output(factory)
    window.thermal_panel.update_temps(_temps())
    window.render_composite()
    assert window.output_warning() == "thermal feed: /dev/video13 does not exist"
