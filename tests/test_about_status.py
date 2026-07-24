"""Tests for the About dialog + status-bar device indicators."""

from __future__ import annotations

from benchhud import __version__
from benchhud.config import Config
from benchhud.shell.about import about_html
from benchhud.shell.main_window import MainWindow


def test_about_html_has_version_and_repo() -> None:
    html = about_html()
    assert __version__ in html
    assert "github.com/IamMrCupp/benchhud" in html
    assert "PolyForm Small Business" in html


def test_window_has_about_action(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.about_action.text() == "About benchhud"


def test_open_about_returns_dialog(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    box = window.open_about()
    assert __version__ in box.text()
    box.close()


def test_status_bar_shows_configured_devices(qtbot) -> None:
    config = Config()
    config.panels.scope.device = "/dev/video4"
    config.panels.telemetry.port = "/dev/ttyUSB0"
    window = MainWindow(config)
    qtbot.addWidget(window)
    message = window.statusBar().currentMessage()
    assert "/dev/video4" in message
    assert "/dev/ttyUSB0" in message
    assert __version__ in message


def test_refresh_status_reflects_config_change(qtbot) -> None:
    config = Config()
    window = MainWindow(config)
    qtbot.addWidget(window)
    config.panels.scope.device = "/dev/videoX"
    window._refresh_status()
    assert "/dev/videoX" in window.statusBar().currentMessage()
