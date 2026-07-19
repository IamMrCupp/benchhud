"""Dynamic device discovery — cameras and serial ports for the settings UI."""

from benchhud.devices.discovery import (
    CameraDevice,
    DeviceScanner,
    SerialDevice,
    SystemDeviceScanner,
    list_serial_ports,
)

__all__ = [
    "CameraDevice",
    "DeviceScanner",
    "SerialDevice",
    "SystemDeviceScanner",
    "list_serial_ports",
]
