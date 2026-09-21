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


def test_qt_enumeration_without_an_application_is_safe():
    """QMediaDevices *segfaults* with no QCoreApplication — never call it there."""
    from benchhud.devices.discovery import list_cameras_qt

    assert list_cameras_qt(app_instance=lambda: None) == []


# --- #184: the index OpenCV opens a camera by ---------------------------

# Qt's order on the maintainer's Mac once OBS's camera extension was running.
_QT_ORDER_WITH_OBS = [
    ("7626645E-4425-469E-9D8B-97E0FA59AC75", "OBS Virtual Camera"),
    ("6C707041-05AC-0010-0007-000000000001", "MacBook Pro Camera"),
    ("A1D22F22-D8CB-4BE2-98E0-A2B000000001", "Eris Camera"),
]


def test_macos_index_is_the_uniqueid_rank_opencv_uses():
    """OpenCV sorts AVFoundation devices by uniqueID; Qt's position is not its index."""
    from benchhud.devices.discovery import cameras_from_qt

    cameras = {c.label: c.index for c in cameras_from_qt(_QT_ORDER_WITH_OBS, "darwin")}
    assert cameras == {"MacBook Pro Camera": 0, "Eris Camera": 2}  # OBS holds index 1


def test_macos_never_offers_benchhuds_own_output_as_a_camera():
    """Reading the OBS Virtual Camera back is a feedback loop."""
    from benchhud.devices.discovery import cameras_from_qt

    labels = [c.label for c in cameras_from_qt(_QT_ORDER_WITH_OBS, "darwin")]
    assert "OBS Virtual Camera" not in labels


def test_resolving_the_macbook_no_longer_opens_obs():
    from benchhud.devices.discovery import cameras_from_qt, resolve_capture_device

    cameras = cameras_from_qt(_QT_ORDER_WITH_OBS, "darwin")
    assert resolve_capture_device("6C707041-05AC-0010-0007-000000000001", cameras) == 0
    assert resolve_capture_device("A1D22F22-D8CB-4BE2-98E0-A2B000000001", cameras) == 2


def test_other_platforms_keep_enumeration_order_and_all_devices():
    from benchhud.devices.discovery import cameras_from_qt

    cameras = cameras_from_qt(_QT_ORDER_WITH_OBS, "win32")
    assert [(c.label, c.index) for c in cameras] == [
        ("OBS Virtual Camera", 0),
        ("MacBook Pro Camera", 1),
        ("Eris Camera", 2),
    ]


def test_opencv_indices_without_obs_match_either_order():
    """Why this hid until the reboot: with no OBS device the two orders agree."""
    from benchhud.devices.discovery import opencv_indices

    ids = [
        "6C707041-05AC-0010-0007-000000000001",
        "A1D22F22-D8CB-4BE2-98E0-A2B000000001",
    ]
    assert opencv_indices(ids, "darwin") == [0, 1]
