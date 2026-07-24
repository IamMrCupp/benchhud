"""Tests for demo mode (synthetic signal + shell wiring)."""

from __future__ import annotations

import numpy as np

from benchhud.demo import DemoSignal
from benchhud.shell.main_window import MainWindow


def test_thermal_shape_and_range() -> None:
    sig = DemoSignal(width=256, height=192)
    temps = sig.thermal()
    assert temps.shape == (192, 256)
    assert temps.dtype == np.float32
    assert temps.min() >= 27.0  # ~ambient
    assert temps.max() > 60.0  # a real hotspot


def test_thermal_frames_advance() -> None:
    sig = DemoSignal()
    first = sig.thermal()
    sig.step()
    assert not np.array_equal(first, sig.thermal())  # hotspot moved


def test_telemetry_snapshot() -> None:
    sig = DemoSignal()
    snap = sig.telemetry()
    assert snap.ok
    assert snap.voltage is not None
    assert snap.current is not None and snap.current >= 0.0
    assert snap.dmm is not None and snap.dmm.mode == "VDC"


def test_demo_action_and_toggle(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.demo_action.text() == "Demo mode"
    assert window.demo_active is False

    window.demo_action.setChecked(True)  # triggers set_demo_mode(True)
    assert window.demo_active is True
    assert "DEMO MODE" in window.statusBar().currentMessage()

    window.demo_action.setChecked(False)
    assert window.demo_active is False
    assert "DEMO MODE" not in window.statusBar().currentMessage()


def test_demo_tick_feeds_panels(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_demo_mode(True)
    try:
        window._demo_tick()
        assert window.thermal_panel.has_frame is True
        # the meter got a real reading, not the placeholder
        assert any(c.isdigit() for c in window.power_panel.voltage_text)
    finally:
        window.set_demo_mode(False)
