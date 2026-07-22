"""Layout versioning: a stale saved layout must not override a newer default."""

from __future__ import annotations

from PySide6.QtCore import Qt

from benchhud.config import Config
from benchhud.shell.main_window import LAYOUT_VERSION, MainWindow


def test_persist_stamps_layout_version(qtbot) -> None:
    config = Config()
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.persist_into_config()
    assert config.shell.layout is not None
    assert config.shell.layout_version == LAYOUT_VERSION


def test_current_version_layout_is_restored(qtbot) -> None:
    config = Config()
    first = MainWindow(config)
    qtbot.addWidget(first)
    # Move the meter to the left, then persist that arrangement.
    first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, first.panels.dock("power"))
    first.persist_into_config()

    second = MainWindow(config)
    qtbot.addWidget(second)
    area = second.dockWidgetArea(second.panels.dock("power"))
    assert area == Qt.DockWidgetArea.LeftDockWidgetArea


def test_stale_version_layout_is_discarded(qtbot) -> None:
    config = Config()
    first = MainWindow(config)
    qtbot.addWidget(first)
    first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, first.panels.dock("power"))
    first.persist_into_config()

    config.shell.layout_version = 0  # saved by an older default arrangement
    rebuilt = MainWindow(config)
    qtbot.addWidget(rebuilt)
    # Falls back to the default arrangement (meter on the right), not the stale one.
    area = rebuilt.dockWidgetArea(rebuilt.panels.dock("power"))
    assert area == Qt.DockWidgetArea.RightDockWidgetArea


def test_reset_layout_clears_saved_layout(qtbot) -> None:
    config = Config()
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.persist_into_config()
    assert config.shell.layout is not None

    window.reset_layout()
    assert config.shell.layout is None
    assert config.shell.layout_version == LAYOUT_VERSION


def test_bogus_layout_does_not_crash(qtbot) -> None:
    config = Config()
    config.shell.layout = "not-valid-base64-state"
    config.shell.layout_version = LAYOUT_VERSION
    window = MainWindow(config)  # must not raise
    qtbot.addWidget(window)
    assert "power" in window.panels.panel_ids()
