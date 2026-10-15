"""Cameras by stable name, so a reconnect doesn't lose them (#257)."""

from __future__ import annotations

import os

from benchhud.config import Config
from benchhud.devices.discovery import (
    CameraDevice,
    list_cameras_v4l2,
    same_device,
    stable_path,
)
from benchhud.jobs.store import JobStore
from benchhud.shell import main_window as mw
from benchhud.shell.preferences import PreferencesDialog

THERMAL_ID = "usb-Sipeed_Ltd._Thermal_Camera__UVC__0123456789-video-index0"
SCOPE_ID = "usb-YW3609A_YW3609A_985200000000100-video-index0"


def fake_dev(tmp_path):
    """A /dev with video nodes and udev's by-id links, as on the bench."""
    dev = tmp_path / "dev"
    by_id = dev / "v4l" / "by-id"
    by_id.mkdir(parents=True)
    for n in (2, 6, 7):
        (dev / f"video{n}").touch()
    os.symlink(dev / "video6", by_id / THERMAL_ID)
    os.symlink(dev / "video2", by_id / SCOPE_ID)
    return dev, by_id


def test_stable_path_finds_the_link(tmp_path) -> None:
    dev, by_id = fake_dev(tmp_path)
    assert stable_path(str(dev / "video6"), str(by_id)) == str(by_id / THERMAL_ID)
    assert stable_path(str(dev / "video7"), str(by_id)) is None  # metadata node: no link
    assert stable_path(str(dev / "video6"), str(tmp_path / "missing")) is None


def test_the_link_follows_a_renumbered_camera(tmp_path) -> None:
    """The overnight case: the thermal comes back as video7."""
    dev, by_id = fake_dev(tmp_path)
    link = by_id / THERMAL_ID
    link.unlink()
    os.symlink(dev / "video7", link)
    assert same_device(str(link), str(dev / "video7"))
    assert not same_device(str(link), str(dev / "video6"))


def test_discovery_identifies_by_stable_name(tmp_path) -> None:
    dev, by_id = fake_dev(tmp_path)
    cams = list_cameras_v4l2(
        glob_fn=lambda _p: [str(dev / "video2"), str(dev / "video6"), str(dev / "video7")],
        name_fn=lambda p: {"video2": "YW3609A", "video6": "Thermal Camera"}.get(p[-6:]),
        stable_fn=lambda p: stable_path(p, str(by_id)),
    )
    assert [c.identifier for c in cams] == [
        str(by_id / SCOPE_ID),
        str(by_id / THERMAL_ID),
        str(dev / "video7"),  # no link: the plain path, as before
    ]
    assert cams[1].label == "Thermal Camera"


def test_the_window_converts_configured_paths_on_linux(qtbot, tmp_path, monkeypatch) -> None:
    dev, by_id = fake_dev(tmp_path)
    config = Config()
    config.panels.scope.device = str(dev / "video2")
    config.panels.thermal.device = "/dev/video5"  # stale: nothing behind it
    window = mw.MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    monkeypatch.setattr(mw.sys, "platform", "linux")
    window._stabilise_camera_paths(stable=lambda p: stable_path(p, str(by_id)))
    assert config.panels.scope.device == str(by_id / SCOPE_ID)
    assert config.panels.thermal.device == "/dev/video5"  # left for the operator


def test_not_on_linux_nothing_changes(qtbot, monkeypatch) -> None:
    config = Config()
    config.panels.scope.device = "/dev/video2"
    window = mw.MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    monkeypatch.setattr(mw.sys, "platform", "darwin")
    window._stabilise_camera_paths(stable=lambda _p: "/dev/v4l/by-id/x")
    assert config.panels.scope.device == "/dev/video2"


def test_labels_and_preferences_match_either_form(qtbot, tmp_path) -> None:
    dev, by_id = fake_dev(tmp_path)
    link = str(by_id / THERMAL_ID)
    cams = [CameraDevice(identifier=link, label="Thermal Camera", index=1)]
    assert mw.camera_label(str(dev / "video6"), cams) == "Thermal Camera"

    class Scanner:
        def cameras(self):
            return cams

        def serial_ports(self):
            return []

    config = Config()
    config.panels.thermal.device = str(dev / "video6")  # an old-style path
    dialog = PreferencesDialog(config, Scanner(), camera_modes=lambda _d: [])
    qtbot.addWidget(dialog)
    assert dialog.thermal_combo.currentData() == link  # recognised, not "not detected"
