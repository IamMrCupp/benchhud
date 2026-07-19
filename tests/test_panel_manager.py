"""Tests for the panel base class and dock manager."""

from __future__ import annotations

import pytest

from benchhud.shell.main_window import MainWindow
from benchhud.shell.panel import Panel
from benchhud.shell.panel_manager import PanelManager
from benchhud.shell.panels.placeholder import PlaceholderPanel


class _TwoPanel(Panel):
    panel_id = "two"
    panel_title = "Two"


class _NoIdPanel(Panel):
    pass  # panel_id left empty on purpose


def test_panel_requires_id(qtbot) -> None:
    with pytest.raises(ValueError, match="panel_id"):
        _NoIdPanel()


def test_panel_sets_object_name(qtbot) -> None:
    panel = PlaceholderPanel()
    qtbot.addWidget(panel)
    assert panel.objectName() == "panel__placeholder"


def test_add_panel_registers_dock_with_stable_name(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    manager = PanelManager(window)
    dock = manager.add_panel(_TwoPanel())
    assert dock.objectName() == "dock__two"
    assert manager.dock("two") is dock
    assert "two" in manager.panel_ids()


def test_duplicate_panel_id_raises(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    manager = PanelManager(window)
    manager.add_panel(_TwoPanel())
    with pytest.raises(ValueError, match="duplicate panel id"):
        manager.add_panel(_TwoPanel())


def test_main_window_hosts_scope_and_telemetry_panels(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    ids = window.panels.panel_ids()
    assert "scope" in ids
    assert "telemetry" in ids
    assert window.panels.dock("scope") is not None
    assert window.panels.dock("telemetry") is not None


def test_layout_persists_and_restores_without_error(qtbot) -> None:
    from benchhud.config import Config

    config = Config()
    first = MainWindow(config)
    qtbot.addWidget(first)
    first.persist_into_config()
    assert config.shell.layout  # dock state captured

    # Reconstructing with the saved layout must restore cleanly.
    second = MainWindow(config)
    qtbot.addWidget(second)
    assert "scope" in second.panels.panel_ids()
