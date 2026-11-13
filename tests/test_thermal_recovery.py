"""A thermal camera that boots in its firmware's Recovery Mode gets power-cycled (#362)."""

from __future__ import annotations

from benchhud.config import Config
from benchhud.devices.recovery import (
    RecoveryDevice,
    device_name,
    port_spec,
    recovery_device,
    recovery_devices,
)
from benchhud.shell.main_window import (
    THERMAL_RECOVERY_CHECK_S,
    THERMAL_RECOVERY_RESET_S,
    MainWindow,
)

CYCLE = ["sudo", "-n", "uhubctl", "-l", "1-4", "-p", "4", "-a", "cycle", "-d", "3"]


def fake_sys(tmp_path, thermal_product="Thermal Camera (Recovery Mode)"):
    """/sys/bus/usb/devices as on repair-bench (2026-11-12), with its interfaces."""
    usb = tmp_path / "devices"
    devices = {
        "usb1": "xHCI Host Controller",
        "1-4": "USB2.0 Hub",
        "1-4.4": thermal_product,
        "3-1": "EMEET PIXY",
        "1-2": None,  # a device with no product string
    }
    for name, product in devices.items():
        (usb / name).mkdir(parents=True)
        if product is not None:
            (usb / name / "product").write_text(product + "\n")
    (usb / "1-4.4:1.0").mkdir()  # an interface
    (usb / "1-4.4:1.0" / "product").write_text("Recovery Mode\n")
    return str(usb)


def test_port_spec_and_device_name_round_trip() -> None:
    assert port_spec("1-4.4") == "1-4:4"
    assert port_spec("1-4.3.2.4") == "1-4.3.2:4"
    assert port_spec("1-2") == "1:2"
    for spec in ("1-4:4", "1-4.3.2:4", "1:2"):
        assert port_spec(device_name(spec)) == spec
    assert device_name(" 1-4:4 ") == "1-4.4"


def test_finds_the_camera_in_recovery_mode(tmp_path) -> None:
    usb = fake_sys(tmp_path)
    found = recovery_devices(usb)
    assert found == [RecoveryDevice("1-4.4", "Thermal Camera (Recovery Mode)")]
    assert found[0].port_spec == "1-4:4"
    assert recovery_device("", usb) == found[0]
    assert recovery_device("1-4:4", usb) == found[0]
    assert recovery_device("1-4:3", usb) is None  # a different port: not ours


def test_a_working_camera_is_not_in_recovery_mode(tmp_path) -> None:
    usb = fake_sys(tmp_path, "Thermal Camera (UVC)")
    assert recovery_devices(usb) == []
    assert recovery_device("1-4:4", usb) is None
    assert recovery_devices(str(tmp_path / "nosys")) == []


def _window(qtbot, reset_port: str, device: RecoveryDevice | None):
    config = Config()
    config.panels.thermal.reset_port = reset_port
    window = MainWindow(config)
    qtbot.addWidget(window)
    ran: list[list[str]] = []
    window.reset_runner = ran.append
    asked: list[str] = []

    def probe(spec: str) -> RecoveryDevice | None:
        asked.append(spec)
        return device

    window.recovery_probe = probe
    return window, ran, asked


RECOVERING = RecoveryDevice("1-4.4", "Thermal Camera (Recovery Mode)")


def test_recovery_mode_cycles_the_port_once_a_minute(qtbot) -> None:
    window, ran, asked = _window(qtbot, "1-4:4", RECOVERING)
    window._check_thermal_recovery(now=100.0)
    qtbot.waitUntil(lambda: len(ran) == 1, timeout=2000)
    assert ran[0] == CYCLE
    assert asked == ["1-4:4"]
    assert "power-cycling its port (1-4:4)" in window.statusBar().currentMessage()
    # Checked every few seconds, but not cycled again while it boots.
    window._check_thermal_recovery(now=100.0 + THERMAL_RECOVERY_CHECK_S / 2)
    assert len(asked) == 1  # too soon to look again
    window._check_thermal_recovery(now=100.0 + THERMAL_RECOVERY_CHECK_S)
    assert len(asked) == 2
    window._check_thermal_recovery(now=100.0 + THERMAL_RECOVERY_RESET_S)
    qtbot.waitUntil(lambda: len(ran) == 2, timeout=2000)


def test_recovery_mode_without_a_port_says_what_to_set(qtbot) -> None:
    window, ran, _ = _window(qtbot, "", RECOVERING)
    window._check_thermal_recovery(now=100.0)
    message = window.statusBar().currentMessage()
    assert "Thermal Camera (Recovery Mode)" in message
    assert 'reset_port = "1-4:4"' in message
    qtbot.wait(50)
    assert ran == []  # never cycles a port it wasn't given


def test_no_recovery_mode_no_cycle(qtbot) -> None:
    window, ran, asked = _window(qtbot, "1-4:4", None)
    window._check_thermal_recovery(now=100.0)
    qtbot.wait(50)
    assert asked == ["1-4:4"]
    assert ran == []
