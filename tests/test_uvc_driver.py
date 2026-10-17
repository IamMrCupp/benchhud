"""The torn-frame warning (#297)."""

from __future__ import annotations

import pytest

from benchhud.capture.uvc_driver import TORN_FRAMES_WARNING, passes_torn_frames


@pytest.mark.parametrize(
    ("value", "torn"), [("1\n", True), ("Y\n", True), ("0\n", False), ("N", False)]
)
def test_reads_the_driver_parameter(value, torn) -> None:
    assert passes_torn_frames(lambda: value, platform="linux") is torn


def test_unknown_is_not_a_warning() -> None:
    def missing() -> str:
        raise FileNotFoundError("uvcvideo not loaded")

    assert passes_torn_frames(missing, platform="linux") is False
    assert passes_torn_frames(lambda: "1", platform="darwin") is False


def _thermal_window(qtbot, torn: bool):
    from benchhud.capture.fallback import FallbackFrameSource
    from benchhud.config import Config
    from benchhud.shell.main_window import MainWindow

    config = Config()
    config.panels.thermal.enabled = True
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.uvc_passes_torn_frames = lambda: torn
    window.start_thermal(lambda: FallbackFrameSource(None, None))
    return window


def test_the_status_bar_warns_while_the_thermal_runs(qtbot) -> None:
    window = _thermal_window(qtbot, torn=True)
    try:
        assert TORN_FRAMES_WARNING in window.statusBar().currentMessage()
    finally:
        window.stop_thermal()
    window._refresh_status()
    assert TORN_FRAMES_WARNING not in window.statusBar().currentMessage()


def test_no_warning_when_the_driver_drops_them(qtbot) -> None:
    window = _thermal_window(qtbot, torn=False)
    try:
        assert TORN_FRAMES_WARNING not in window.statusBar().currentMessage()
    finally:
        window.stop_thermal()
