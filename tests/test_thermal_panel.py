"""Tests for the thermal panel (hardware-free)."""

from __future__ import annotations

import numpy as np

from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.thermal import ThermalPanel
from benchhud.thermal.convert import y16_to_celsius


def test_panel_metadata(qtbot) -> None:
    panel = ThermalPanel()
    qtbot.addWidget(panel)
    assert panel.panel_id == "thermal"
    assert panel.objectName() == "panel__thermal"


def test_starts_with_no_signal(qtbot) -> None:
    panel = ThermalPanel()
    qtbot.addWidget(panel)
    assert panel.has_frame is False
    assert "—" in panel.readout_text


def test_update_temps_renders_and_reports_hotspot(qtbot) -> None:
    panel = ThermalPanel()
    qtbot.addWidget(panel)
    temps = np.full((48, 64), 25.0, dtype=np.float32)
    temps[10, 20] = 91.4
    panel.update_temps(temps)
    assert panel.has_frame is True
    assert "91.4" in panel.readout_text
    assert "(20, 10)" in panel.readout_text  # x=20, y=10


def test_update_from_y16(qtbot) -> None:
    panel = ThermalPanel()
    qtbot.addWidget(panel)
    raw = np.full((16, 16), 19200, dtype=np.uint16)  # 300 K -> 26.85 °C
    panel.update_temps(y16_to_celsius(raw))
    assert panel.has_frame is True
    assert "26.8" in panel.readout_text or "26.9" in panel.readout_text


def test_ignores_none(qtbot) -> None:
    panel = ThermalPanel()
    qtbot.addWidget(panel)
    panel.update_temps(None)
    assert panel.has_frame is False


def test_shell_hosts_thermal_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert "thermal" in window.panels.panel_ids()
