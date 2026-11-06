"""Dynamic device discovery — cameras and serial ports for the settings UI."""

from benchhud.devices.discovery import (
    CameraDevice,
    DeviceScanner,
    SerialDevice,
    SystemDeviceScanner,
    UsbInterface,
    list_serial_ports,
    list_usb_interfaces,
)

__all__ = [
    "CameraDevice",
    "DeviceScanner",
    "SerialDevice",
    "SystemDeviceScanner",
    "UsbInterface",
    "list_serial_ports",
    "list_usb_interfaces",
]
