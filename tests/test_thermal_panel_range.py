"""The thermal panel follows the board, and cameras show once (#269)."""

from __future__ import annotations

import cv2
import numpy as np

from benchhud.config import Config
from benchhud.devices.discovery import list_cameras_v4l2
from benchhud.jobs.store import JobStore
from benchhud.shell import main_window as mw
from benchhud.shell.panels.thermal import ThermalPanel


def bench_board() -> np.ndarray:
    """What the T256s saw on the bench: 20.5 to 32 °C."""
    temps = np.full((192, 256), 22.0, dtype=np.float32)
    temps[0, 0] = 20.5
    cv2.circle(temps, (134, 67), 12, 32.0, -1)
    return temps


def test_auto_range_brightens_a_warm_board(qtbot) -> None:
    fixed = ThermalPanel()
    qtbot.addWidget(fixed)
    fixed.set_style(auto_range=False, tmin=20.0, tmax=100.0, colormap=cv2.COLORMAP_INFERNO)
    fixed.update_temps(bench_board())
    auto = ThermalPanel()
    qtbot.addWidget(auto)
    auto.update_temps(bench_board())  # auto is the default
    hot = auto._source_pixmap.toImage().pixelColor(134, 67).lightness()
    dark_hot = fixed._source_pixmap.toImage().pixelColor(134, 67).lightness()
    assert hot > dark_hot + 80  # the hot part now reads near the top of the palette
    assert "scale 22 to 32°C" in auto.readout_text  # the median to the peak


def test_the_panel_follows_the_controls(qtbot) -> None:
    from benchhud.overlay.config_adapter import colormap_from_name

    config = Config()
    config.overlay.colormap = "VIRIDIS"
    config.overlay.auto_range = False
    config.overlay.tmin, config.overlay.tmax = 25.0, 35.0
    window = mw.MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    panel = window.thermal_panel
    assert panel._colormap == colormap_from_name("VIRIDIS")
    assert not panel._auto and (panel._tmin, panel._tmax) == (25.0, 35.0)
    window.controls_panel.range_combo.setCurrentText("Auto")
    assert panel._auto


def test_metadata_and_loopback_nodes_are_not_cameras() -> None:
    nodes = {
        "/dev/video0": (True, False),  # EMEET picture
        "/dev/video1": (False, False),  # EMEET metadata
        "/dev/video3": (True, False),  # thermal picture
        "/dev/video6": (False, False),  # thermal metadata (interface 1.1)
        "/dev/video10": (True, True),  # benchhud's own loopback output
        "/dev/video9": (None, False),  # couldn't ask: kept, as before
    }
    cams = list_cameras_v4l2(
        glob_fn=lambda _p: list(nodes),
        name_fn=lambda p: p,
        stable_fn=lambda _p: None,
        capture_fn=lambda p: nodes[p][0],
        virtual_fn=lambda p: nodes[p][1],
    )
    assert [c.identifier for c in cams] == ["/dev/video0", "/dev/video3", "/dev/video9"]


def test_migration_never_lands_on_a_metadata_node(qtbot, monkeypatch) -> None:
    config = Config()
    config.panels.thermal.device = "/dev/video6"  # the old number, now a metadata node
    window = mw.MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    monkeypatch.setattr(mw.sys, "platform", "linux")
    monkeypatch.setattr(mw, "can_capture", lambda p: p != "/dev/video6")
    window._stabilise_camera_paths(stable=lambda _p: "/dev/v4l/by-id/x-video-index1")
    assert config.panels.thermal.device == "/dev/video6"  # left for the operator to re-pick
