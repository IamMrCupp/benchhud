"""Enumerate cameras and serial ports so the UI can offer dropdowns.

Serial ports come from ``pyserial``'s cross-platform ``list_ports``. Cameras are
enumerated per-OS: on Linux we glob ``/dev/video*`` and read the human name from
``/sys`` (exact device paths, which is what cv2 / v4l2 open — and what the raw
Y16 thermal workflow needs); elsewhere we use Qt Multimedia's ``QMediaDevices``.

Everything is behind :class:`DeviceScanner` so the settings UI depends on the
interface, not the OS — tests inject a fake scanner, and the discovery helpers
take injectable dependencies so they run without real hardware.
"""

from __future__ import annotations

import glob as glob_module
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SerialDevice:
    device: str  # e.g. /dev/ttyUSB0
    description: str  # human label


@dataclass(frozen=True)
class CameraDevice:
    identifier: str  # /dev/videoN on Linux, else a Qt device id
    label: str  # human label
    index: int  # the index cv2.VideoCapture opens this camera by (see list_cameras_qt)


#: OBS's virtual camera (the device benchhud *publishes* to on macOS). Reading it
#: back as a capture camera is a feedback loop, so it's never offered as one.
#: Same UID as ``output.cmio.OBS_DEVICE_UID``, kept here so discovery stays
#: free of the ctypes output module.
OBS_VIRTUAL_CAMERA_UID = "7626645E-4425-469E-9D8B-97E0FA59AC75"


def opencv_indices(identifiers: Sequence[str], platform: str = sys.platform) -> list[int]:
    """The index OpenCV opens each camera by, in the order ``identifiers`` were given.

    macOS: OpenCV's AVFoundation backend takes ``devicesWithMediaType:`` and
    **sorts it by uniqueID**, and Qt's camera id *is* that uniqueID. So the
    index is the id's rank in sorted order, not its position in Qt's list (#184).
    Elsewhere, enumeration order is what we have.
    """
    if platform == "darwin":
        ranked = sorted(identifiers)
        return [ranked.index(identifier) for identifier in identifiers]
    return list(range(len(identifiers)))


@runtime_checkable
class DeviceScanner(Protocol):
    """Something that can enumerate cameras and serial ports."""

    def cameras(self) -> list[CameraDevice]: ...
    def serial_ports(self) -> list[SerialDevice]: ...


def list_serial_ports(comports: Callable[[], list] | None = None) -> list[SerialDevice]:
    """Enumerate serial ports (``comports`` injectable for tests)."""
    if comports is None:
        from serial.tools import list_ports

        comports = list_ports.comports
    return [
        SerialDevice(device=p.device, description=(p.description or p.device)) for p in comports()
    ]


def _read_sysfs_name(path: str) -> str | None:
    """Read a V4L2 node's human name from /sys, or None if unavailable."""
    node = Path(path).name
    try:
        return Path(f"/sys/class/video4linux/{node}/name").read_text().strip()
    except OSError:
        return None


def list_cameras_v4l2(
    glob_fn: Callable[[str], list[str]] = glob_module.glob,
    name_fn: Callable[[str], str | None] | None = None,
) -> list[CameraDevice]:
    """Linux: enumerate /dev/video* with names (deps injectable for tests).

    ``name_fn(path)`` returns a human name or None; the label falls back to the
    path. Note: metadata nodes (odd-numbered on many UVC cams) also appear here —
    the /sys label helps pick the capture node; a capability filter is a later
    refinement.
    """
    read = name_fn or _read_sysfs_name
    cameras = []
    for index, path in enumerate(sorted(glob_fn("/dev/video*"))):
        cameras.append(CameraDevice(identifier=path, label=(read(path) or path), index=index))
    return cameras


def list_cameras_qt(app_instance: Callable[[], object | None] | None = None) -> list[CameraDevice]:
    """Non-Linux: enumerate cameras via Qt Multimedia.

    Returns nothing when there is no Qt application — QMediaDevices *segfaults*
    without one, which a caller can't catch, so guard rather than crash a CLI.
    """
    from PySide6.QtCore import QCoreApplication

    instance = app_instance or QCoreApplication.instance
    if instance() is None:
        return []

    from PySide6.QtMultimedia import QMediaDevices

    inputs = [
        (cam.id().data().decode(errors="replace"), cam.description())
        for cam in QMediaDevices.videoInputs()
    ]
    return cameras_from_qt(inputs)


def cameras_from_qt(
    inputs: Sequence[tuple[str, str]], platform: str = sys.platform
) -> list[CameraDevice]:
    """Build the camera list from Qt's ``(id, description)`` pairs (pure, testable).

    Indices are computed over *every* device, including OBS's virtual camera,
    because OpenCV counts it too. Only then is that camera dropped from the list.
    """
    identifiers = [identifier for identifier, _label in inputs]
    indices = opencv_indices(identifiers, platform)
    return [
        CameraDevice(identifier=identifier, label=label, index=index)
        for (identifier, label), index in zip(inputs, indices, strict=True)
        if not (platform == "darwin" and identifier.upper() == OBS_VIRTUAL_CAMERA_UID)
    ]


def resolve_capture_device(identifier: str, cameras: Sequence[CameraDevice]) -> int | str:
    """Map a configured camera identifier to something OpenCV can actually open.

    Linux identifiers are already device paths, which ``cv2.VideoCapture`` takes
    directly. Qt's ids (macOS/Windows) are strings it cannot open at all, so we
    use the index discovery computed for OpenCV (on macOS, the id's rank in
    uniqueID order; see :func:`opencv_indices`). Without this, choosing a camera
    in Preferences on macOS silently yields no feed, or the wrong one.
    """
    text = identifier.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    if text.startswith("/dev/"):
        return text
    for camera in cameras:
        if camera.identifier == text:
            return camera.index
    return text  # unknown: let the caller's open attempt fail and fall back


def list_cameras() -> list[CameraDevice]:
    """Enumerate cameras using the right backend for this OS."""
    if sys.platform.startswith("linux"):
        return list_cameras_v4l2()
    return list_cameras_qt()


class SystemDeviceScanner:
    """The real :class:`DeviceScanner` — scans the live system."""

    def cameras(self) -> list[CameraDevice]:
        return list_cameras()

    def serial_ports(self) -> list[SerialDevice]:
        return list_serial_ports()
