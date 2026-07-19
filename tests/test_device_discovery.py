"""Tests for device discovery (hardware-free via injected deps)."""

from __future__ import annotations

from dataclasses import dataclass

from benchhud.devices.discovery import (
    CameraDevice,
    DeviceScanner,
    SerialDevice,
    SystemDeviceScanner,
    list_cameras_v4l2,
    list_serial_ports,
)


@dataclass
class _FakePort:
    device: str
    description: str


def test_list_serial_ports_maps_fields() -> None:
    ports = [
        _FakePort("/dev/ttyUSB0", "CH340 serial"),
        _FakePort("/dev/ttyACM0", ""),  # empty description -> falls back to device
    ]
    result = list_serial_ports(comports=lambda: ports)
    assert result[0] == SerialDevice("/dev/ttyUSB0", "CH340 serial")
    assert result[1] == SerialDevice("/dev/ttyACM0", "/dev/ttyACM0")


def test_list_cameras_v4l2_reads_names_and_sorts() -> None:
    nodes = ["/dev/video6", "/dev/video4", "/dev/video0"]
    names = {
        "/dev/video0": "ClearClick capture",
        "/dev/video4": "YW3609A",
        "/dev/video6": "Thermal Camera (UVC)",
    }
    cams = list_cameras_v4l2(glob_fn=lambda pat: nodes, name_fn=lambda p: names[p])
    # Sorted by path.
    assert [c.identifier for c in cams] == ["/dev/video0", "/dev/video4", "/dev/video6"]
    assert cams[2].label == "Thermal Camera (UVC)"
    assert cams[2].index == 2


def test_v4l2_name_falls_back_to_path() -> None:
    cams = list_cameras_v4l2(glob_fn=lambda pat: ["/dev/video9"], name_fn=lambda p: None)
    assert cams[0].label == "/dev/video9"


def test_system_scanner_satisfies_protocol() -> None:
    assert isinstance(SystemDeviceScanner(), DeviceScanner)


def test_system_scanner_real_serial_enumeration_smoke() -> None:
    # Real enumeration must return a list without raising (contents vary by host).
    ports = SystemDeviceScanner().serial_ports()
    assert isinstance(ports, list)
    assert all(isinstance(p, SerialDevice) for p in ports)


def test_camera_device_shape() -> None:
    cam = CameraDevice(identifier="/dev/video4", label="scope", index=1)
    assert cam.identifier == "/dev/video4"
    assert cam.index == 1
