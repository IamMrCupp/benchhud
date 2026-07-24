"""The shell must never claim virtual-camera output is on while frames vanish."""

from __future__ import annotations

import numpy as np

from benchhud.config import Config
from benchhud.output.sink import UNSUPPORTED_PLATFORM, NullSink, open_output_sink
from benchhud.shell.main_window import MainWindow


def test_null_sink_carries_no_reason_by_default() -> None:
    assert NullSink().reason == ""


def test_non_linux_gets_an_explicit_platform_reason(monkeypatch) -> None:
    monkeypatch.setattr("benchhud.output.sink.sys.platform", "darwin")
    sink = open_output_sink("/dev/video10", 320, 240)
    assert isinstance(sink, NullSink)
    assert sink.reason == UNSUPPORTED_PLATFORM
    assert sink.size == (320, 240)


def test_non_linux_sink_still_accepts_frames(monkeypatch) -> None:
    """Falling back must keep the HUD usable, just not publishing."""
    monkeypatch.setattr("benchhud.output.sink.sys.platform", "win32")
    sink = open_output_sink("/dev/video10", 32, 24)
    sink.send(np.zeros((24, 32, 3), dtype=np.uint8))
    assert sink.frame_count == 1


def test_linux_failure_reason_names_the_device(monkeypatch) -> None:
    monkeypatch.setattr("benchhud.output.sink.sys.platform", "linux")

    def _boom(*_a, **_k):
        raise OSError("no such device")

    monkeypatch.setattr("benchhud.output.sink.V4l2LoopbackSink", _boom)
    sink = open_output_sink("/dev/video99", 320, 240)
    assert "/dev/video99" in sink.reason


# --- shell surfacing -----------------------------------------------------


def _window(qtbot, *, enabled: bool, reason: str) -> MainWindow:
    cfg = Config()
    cfg.output.enabled = enabled
    window = MainWindow(cfg)
    qtbot.addWidget(window)
    window.start_output(sink_factory=lambda d, w, h: NullSink(w, h, reason=reason))
    return window


def test_no_warning_when_output_is_deliberately_off(qtbot) -> None:
    window = _window(qtbot, enabled=False, reason=UNSUPPORTED_PLATFORM)
    assert window.output_warning() is None
    assert "inactive" not in window.statusBar().currentMessage()


def test_warning_when_enabled_but_publishing_nothing(qtbot) -> None:
    window = _window(qtbot, enabled=True, reason=UNSUPPORTED_PLATFORM)
    assert window.output_warning() == UNSUPPORTED_PLATFORM
    assert "⚠ virtual cam inactive" in window.statusBar().currentMessage()


def test_no_warning_when_the_sink_is_really_publishing(qtbot) -> None:
    window = _window(qtbot, enabled=True, reason="")
    assert window.output_warning() is None
    assert "inactive" not in window.statusBar().currentMessage()
