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
    config = Config()
    config.shell.window_width = 1024
    config.shell.window_height = 768
    window = MainWindow(config)
    qtbot.addWidget(window)
    assert window.width() == 1024
    assert window.height() == 768


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
