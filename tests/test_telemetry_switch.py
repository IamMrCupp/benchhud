"""A switch for telemetry, and the instrument port by its stable name (#264)."""

from __future__ import annotations

import os
from types import SimpleNamespace

from benchhud.config import Config
from benchhud.devices.discovery import CameraDevice, SerialDevice, list_serial_ports, stable_path
from benchhud.jobs.store import JobStore
from benchhud.shell import main_window as mw
from benchhud.shell.preferences import PreferencesDialog

OWON_ID = "usb-1a86_USB_Serial-if00-port0"


def fake_serial(tmp_path):
    dev = tmp_path / "dev"
    by_id = dev / "serial" / "by-id"
    by_id.mkdir(parents=True)
    (dev / "ttyUSB0").touch()
    (dev / "ttyUSB1").touch()
    os.symlink(dev / "ttyUSB0", by_id / OWON_ID)
    return dev, by_id


def test_ports_are_listed_by_stable_name(tmp_path) -> None:
    dev, by_id = fake_serial(tmp_path)
    ports = list_serial_ports(
        comports=lambda: [
            SimpleNamespace(device=str(dev / "ttyUSB0"), description="USB Serial"),
            SimpleNamespace(device=str(dev / "ttyUSB1"), description=None),
        ],
        stable_fn=lambda p: stable_path(p, str(by_id)),
    )
    assert ports[0] == SerialDevice(str(by_id / OWON_ID), "USB Serial (ttyUSB0)")
    assert ports[1].device == str(dev / "ttyUSB1")  # no link: as before


class Scanner:
    def __init__(self, ports):
        self.ports = ports

    def cameras(self) -> list[CameraDevice]:
        return []

    def serial_ports(self):
        return self.ports


def test_preferences_switch_telemetry_on(qtbot) -> None:
    config = Config()
    config.panels.telemetry.port = "/dev/ttyUSB0"
    assert not config.panels.telemetry.enabled
    dialog = PreferencesDialog(
        config, Scanner([SerialDevice("/dev/ttyUSB0", "USB Serial")]), camera_modes=lambda _d: []
    )
    qtbot.addWidget(dialog)
    assert not dialog.telemetry_enabled.isChecked()
    dialog.telemetry_enabled.setChecked(True)
    dialog.apply_to_config()
    assert config.panels.telemetry.enabled


def test_an_old_port_path_is_still_recognised(qtbot, tmp_path) -> None:
    dev, by_id = fake_serial(tmp_path)
    link = str(by_id / OWON_ID)
    config = Config()
    config.panels.telemetry.port = str(dev / "ttyUSB0")
    dialog = PreferencesDialog(
        config, Scanner([SerialDevice(link, "USB Serial (ttyUSB0)")]), camera_modes=lambda _d: []
    )
    qtbot.addWidget(dialog)
    assert dialog.serial_combo.currentData() == link


def test_the_window_converts_the_port_on_linux(qtbot, tmp_path, monkeypatch) -> None:
    dev, by_id = fake_serial(tmp_path)
    config = Config()
    config.panels.telemetry.port = str(dev / "ttyUSB0")
    window = mw.MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    monkeypatch.setattr(mw.sys, "platform", "linux")
    window._stabilise_camera_paths(
        stable=lambda _p: None, serial_stable=lambda p: stable_path(p, str(by_id))
    )
    assert config.panels.telemetry.port == str(by_id / OWON_ID)


def test_never_a_second_handle_on_the_owon(qtbot, tmp_path) -> None:
    """The OWON saved by its stable name; the terminal pointed at the old spelling."""
    dev, by_id = fake_serial(tmp_path)
    link = str(by_id / OWON_ID)
    config = Config()
    config.panels.telemetry.enabled = True
    config.panels.telemetry.port = link
    config.terminal.target = str(dev / "ttyUSB0")
    window = mw.MainWindow(
        config,
        job_store=JobStore(":memory:"),
        scanner=Scanner(
            [SerialDevice(link, "USB Serial"), SerialDevice(str(dev / "ttyUSB1"), "Other")]
        ),
    )
    qtbot.addWidget(window)
    identity, _factory = window._terminal_target()
    assert identity[0] == "owon:scpi"  # through the session, not a raw second handle
    targets = [value for _label, value in window.terminal_targets()]
    assert link not in targets and str(dev / "ttyUSB0") not in targets
    assert str(dev / "ttyUSB1") in targets
    window.set_terminal_target(str(dev / "ttyUSB0"), 115200, "crlf", False)
    assert config.terminal.target == "owon"
