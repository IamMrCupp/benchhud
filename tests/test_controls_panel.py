"""Tests for the overlay/registration controls panel (hardware-free)."""

from __future__ import annotations

from benchhud.config import Config
from benchhud.overlay.composite import OverlayMode
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.controls import ControlsPanel


def test_initializes_from_config(qtbot) -> None:
    config = Config()
    config.overlay.mode = "tint"
    config.overlay.alpha = 0.4
    config.registration.scale = 1.7
    panel = ControlsPanel(config)
    qtbot.addWidget(panel)

    assert panel.current_settings().mode is OverlayMode.TINT
    assert panel.current_settings().alpha == 0.4
    assert panel.current_transform().scale == 1.7


def test_alpha_slider_updates_settings_and_config(qtbot) -> None:
    config = Config()
    panel = ControlsPanel(config)
    qtbot.addWidget(panel)
    panel.alpha_slider.setValue(80)
    assert panel.current_settings().alpha == 0.8
    assert config.overlay.alpha == 0.8  # write-through


def test_mode_change_updates_config(qtbot) -> None:
    config = Config()
    panel = ControlsPanel(config)
    qtbot.addWidget(panel)
    panel.mode_combo.setCurrentText("marker")
    assert panel.current_settings().mode is OverlayMode.MARKER
    assert config.overlay.mode == "marker"


def test_registration_nudge_updates_config(qtbot) -> None:
    config = Config()
    panel = ControlsPanel(config)
    qtbot.addWidget(panel)
    panel.scale_spin.setValue(2.5)
    panel.rotation_spin.setValue(15.0)
    assert panel.current_transform().scale == 2.5
    assert config.registration.scale == 2.5
    assert config.registration.rotation_deg == 15.0


def test_emits_changed_signal(qtbot) -> None:
    panel = ControlsPanel(Config())
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.changed, timeout=1000):
        panel.alpha_slider.setValue(33)


def test_works_without_config(qtbot) -> None:
    panel = ControlsPanel()  # no config -> defaults, no write-through crash
    qtbot.addWidget(panel)
    panel.threshold_spin.setValue(70.0)
    assert panel.current_settings().threshold == 70.0


def test_shell_hosts_controls_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert "controls" in window.panels.panel_ids()
