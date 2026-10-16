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


def test_a_pause_dims_the_last_frame_rather_than_blanking(qtbot) -> None:
    """The T256s pauses to recalibrate; flashing "No signal" each time flickered (#276)."""
    from benchhud.shell.panels.thermal import PAUSED_SUFFIX

    panel = ThermalPanel()
    qtbot.addWidget(panel)
    panel.update_temps(np.full((48, 64), 30.0, dtype=np.float32))
    panel.set_paused(True)
    assert panel.paused and panel.has_frame
    assert panel.readout_text.endswith(PAUSED_SUFFIX)
    panel.set_paused(True)
    assert panel.readout_text.count(PAUSED_SUFFIX) == 1
    panel.update_temps(np.full((48, 64), 31.0, dtype=np.float32))
    assert not panel.paused
    assert PAUSED_SUFFIX not in panel.readout_text


def test_the_readout_never_resizes_the_panel(qtbot) -> None:
    """The readout's width moved with the hot spot, and the dock with it (#294)."""
    panel = ThermalPanel()
    qtbot.addWidget(panel)
    widths, heights = set(), set()
    for x, y in [(1, 236), (191, 251), (138, 248), (144, 200)]:
        temps = np.full((256, 192), 23.0, dtype=np.float32)
        temps[y, x] = 30.1
        panel.update_temps(temps)
        widths.add(panel.minimumSizeHint().width())
        heights.add(panel.minimumSizeHint().height())
    panel.set_paused(True)
    widths.add(panel.minimumSizeHint().width())
    heights.add(panel.minimumSizeHint().height())
    assert len(widths) == 1 and len(heights) == 1


def test_the_image_doesnt_size_the_panel(qtbot) -> None:
    """A pixmap scaled to the view became its size hint, so the view could only grow."""
    panel = ThermalPanel()
    qtbot.addWidget(panel)
    frame = np.full((256, 192), 25.0, dtype=np.float32)
    panel.update_temps(frame)
    before = panel.sizeHint()
    panel.resize(900, 1200)
    panel.update_temps(frame)
    assert panel.sizeHint() == before
