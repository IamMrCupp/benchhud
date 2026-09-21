"""Headless smoke tests for the HUD shell window (offscreen via pytest-qt)."""

from __future__ import annotations

from benchhud.config import Config
from benchhud.shell.main_window import MainWindow


def test_main_window_constructs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.windowTitle() == "benchhud"
    assert window.centralWidget() is not None


def test_main_window_shows(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()


def test_restores_geometry_from_config(qtbot) -> None:
    # Values fit within the (offscreen 800x800) screen, so no clamp applies.
    config = Config()
    config.shell.window_width = 700
    config.shell.window_height = 600
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window.width() == 700
    assert window.height() == 600


def test_window_clamps_to_screen(qtbot) -> None:
    config = Config()
    config.shell.window_width = 99999  # absurdly large
    config.shell.window_height = 99999
    window = MainWindow(config)
    qtbot.addWidget(window)
    screen = window.screen().availableGeometry()
    assert window.width() <= screen.width()
    assert window.height() <= screen.height()


def test_persist_captures_geometry_and_layout(qtbot) -> None:
    config = Config()
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.resize(1200, 700)
    window.persist_into_config()
    assert config.shell.window_width == 1200
    assert config.shell.window_height == 700
    assert config.shell.layout  # non-empty base64 dock state


def test_close_invokes_persist_callback(qtbot) -> None:
    saved: list[Config] = []
    config = Config()
    window = MainWindow(config, on_persist=saved.append)
    qtbot.addWidget(window)
    window.show()
    window.close()
    assert saved == [config]


def test_construction_starts_no_capture(qtbot) -> None:
    # Constructing the window must not spin up capture threads (tests do it a lot).
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._pump is None


def test_start_preview_feeds_scope_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.start_preview()
    try:
        qtbot.waitUntil(lambda: window.scope_panel.has_frame, timeout=2000)
    finally:
        window.stop_preview()
    assert window.scope_panel.has_frame


def test_stop_preview_is_idempotent(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.start_preview()
    window.stop_preview()
    window.stop_preview()  # must not raise
    assert window._pump is None


def _synthetic_scope():
    from benchhud.capture.fallback import FallbackFrameSource
    from benchhud.capture.frame_source import SyntheticFrameSource

    return FallbackFrameSource(None, SyntheticFrameSource(16, 16), sleep=lambda _s: None)


def test_status_bar_says_when_the_scope_is_a_test_pattern(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.start_preview(_synthetic_scope)
    try:
        assert "⚠ test pattern — no camera selected" in window.statusBar().currentMessage()
    finally:
        window.stop_preview()


def test_preferences_reopen_the_scope_when_the_camera_changes(qtbot) -> None:
    config = Config()
    window = MainWindow(config)
    qtbot.addWidget(window)
    opened: list[str] = []
    window.start_preview(_synthetic_scope)
    window.start_preview = lambda: opened.append(config.panels.scope.device)  # type: ignore[method-assign]
    try:
        window.reconfigure_preview()
        assert opened == []  # same camera: leave the running preview alone

        config.panels.scope.device = "/dev/video9"
        window.reconfigure_preview()
        assert opened == ["/dev/video9"]
    finally:
        window.stop_preview()


def test_preferences_accept_is_wired_to_reconfigure_preview(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    calls: list[bool] = []
    window.reconfigure_preview = lambda: calls.append(True)  # type: ignore[method-assign]
    dialog = window.open_preferences()
    dialog.accept()
    assert calls == [True]


def _thermal_source(temps_live: bool = True):
    """A FallbackFrameSource over a scripted thermal camera."""
    import numpy as np

    from benchhud.capture.fallback import FallbackFrameSource

    class _Cam:
        is_open = True
        size = (4, 3)
        fatal_error = None
        live = temps_live

        def open(self):
            return True

        def read(self):
            return np.full((3, 4), 35.0, dtype=np.float32) if self.live else None

        def release(self):
            pass

        def close(self):
            pass

    cam = _Cam()
    source = FallbackFrameSource(cam, None, name="T256s", stall_timeout=0.2, fallback_fps=100.0)
    return source, cam


def test_start_thermal_is_a_noop_without_a_thermal_camera(qtbot) -> None:
    config = Config()
    config.panels.thermal.enabled = False
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.start_thermal()
    assert window.thermal_status() is None
    assert "thermal —" in window.statusBar().currentMessage()


def test_live_thermal_feeds_the_panel(qtbot) -> None:
    config = Config()
    config.panels.thermal.enabled = True
    config.panels.thermal.device = "/dev/video6"
    window = MainWindow(config)
    qtbot.addWidget(window)
    source, _cam = _thermal_source()
    window.start_thermal(lambda: source)
    try:
        qtbot.waitUntil(lambda: window.thermal_panel.last_temps is not None, timeout=2000)
        assert window.thermal_status().live
        assert "35.0°C" in window.thermal_panel.readout_text
    finally:
        window.stop_thermal()


def test_losing_the_thermal_camera_clears_the_stale_heatmap(qtbot) -> None:
    config = Config()
    config.panels.thermal.enabled = True
    config.panels.thermal.device = "/dev/video6"
    window = MainWindow(config)
    qtbot.addWidget(window)
    source, cam = _thermal_source()
    window.start_thermal(lambda: source)
    try:
        qtbot.waitUntil(
            lambda: window._thermal_status is not None and window._thermal_status.live, timeout=2000
        )
        cam.live = False  # unplugged: OpenCV stays 'open' but frames stop
        qtbot.waitUntil(lambda: window.thermal_panel.last_temps is None, timeout=2000)
        assert "no signal — T256s stopped sending frames" in window.statusBar().currentMessage()
    finally:
        window.stop_thermal()


def test_reconfigure_thermal_starts_and_stops_with_preferences(qtbot) -> None:
    config = Config()
    config.panels.thermal.enabled = False
    window = MainWindow(config)
    qtbot.addWidget(window)
    started: list[str] = []
    real_start = window.start_thermal
    window.start_thermal = lambda: started.append(config.panels.thermal.device)  # type: ignore[method-assign]

    window.reconfigure_thermal()
    assert started == []  # still off: nothing to do

    config.panels.thermal.enabled = True
    config.panels.thermal.device = "/dev/video6"
    window.reconfigure_thermal()
    assert started == ["/dev/video6"]

    window.start_thermal = real_start  # type: ignore[method-assign]
    source, _cam = _thermal_source()
    window.start_thermal(lambda: source)
    window._thermal_device = "/dev/video6"
    config.panels.thermal.enabled = False
    window.reconfigure_thermal()
    assert window.thermal_status() is None  # stopped
