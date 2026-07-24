"""macOS virtual-camera output: publish into OBS's CoreMediaIO camera extension.

macOS has no v4l2loopback. Since macOS 12.3 the supported mechanism is a
**Camera Extension** (CMIOExtension); the old DAL plug-ins are deprecated and
unusable from macOS 14. Authoring and shipping our own extension would mean
Swift/ObjC, an ``.app`` in ``/Applications``, Developer ID signing, notarization
and a paid Apple Developer membership — none of which survives ``pip install``.

So we don't ship one. OBS already installs a camera extension, and its **sink
stream** accepts frames from any process — its ``authorizedToStartStream``
returns true unconditionally, and OBS's own app pushes frames the same way. We
talk to it with Apple's *public* CoreMediaIO API via ``ctypes``: no third-party
library, no compiled extension, and no GPL exposure (the licence problem was
linking ``pyvirtualcam``, not interoperating with OBS across an OS IPC boundary).
That mirrors :mod:`benchhud.output.v4l2`, which talks the kernel uapi directly.

Structure follows the v4l2 backend: the framework calls sit behind a thin,
lazily-loaded layer so importing this module is safe on any OS, and the parts
worth testing — pixel conversion and stream selection — are pure.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass, field

import cv2
import numpy as np

# OBS's camera-extension device, from its Info.plist (OBSCameraDeviceUUID).
# Stable since the extension shipped; verified against OBS 32.1.2.
OBS_DEVICE_UID = "7626645E-4425-469E-9D8B-97E0FA59AC75"

_CMIO_FRAMEWORK = "/System/Library/Frameworks/CoreMediaIO.framework/CoreMediaIO"
_CF_FRAMEWORK = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

#: kCMIOObjectSystemObject — the root object you query for the device list.
_SYSTEM_OBJECT = 1
_CF_STRING_ENCODING_UTF8 = 0x08000100


def _fourcc(code: str) -> int:
    """``'dev#'`` → the UInt32 selector CoreMediaIO expects."""
    return (ord(code[0]) << 24) | (ord(code[1]) << 16) | (ord(code[2]) << 8) | ord(code[3])


_SCOPE_GLOBAL = _fourcc("glob")
_PROP_DEVICES = _fourcc("dev#")
_PROP_DEVICE_UID = _fourcc("uid ")
_PROP_STREAMS = _fourcc("stm#")
_PROP_STREAM_DIRECTION = _fourcc("sdir")

#: kCMIOStreamPropertyDirection: 0 = output stream, 1 = input stream. Empirically
#: every ordinary capture camera reports 1 on the stream apps *read*, so the
#: stream we *write* into — OBS's sink — is the one reporting 0. pyvirtualcam
#: hardcodes ``streams[1]`` instead; querying the direction is not a guess.
_DIRECTION_SINK = 0


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class CmioStream:
    """One stream on a CoreMediaIO device."""

    stream_id: int
    direction: int | None

    @property
    def is_sink(self) -> bool:
        """True when this is the stream a producer writes frames into."""
        return self.direction == _DIRECTION_SINK


@dataclass(frozen=True)
class CmioDevice:
    """One CoreMediaIO device (a camera, or OBS's virtual camera)."""

    device_id: int
    uid: str | None
    streams: tuple[CmioStream, ...] = field(default_factory=tuple)


# --- pure helpers (no framework calls — tested on any OS) -----------------


def select_sink_stream(streams: tuple[CmioStream, ...]) -> CmioStream | None:
    """Pick the stream to push frames into.

    Prefers an explicit direction of "output stream"; falls back to the second
    stream (what OBS's own plugin and pyvirtualcam assume) when the direction
    property is unavailable, so a future OBS that stops reporting it still works.
    """
    for stream in streams:
        if stream.is_sink:
            return stream
    if len(streams) > 1:
        return streams[1]
    return None


def find_obs_device(devices: list[CmioDevice], uid: str = OBS_DEVICE_UID) -> CmioDevice | None:
    """The OBS virtual-camera device, matched by UID (case-insensitive)."""
    wanted = uid.lower()
    return next((d for d in devices if (d.uid or "").lower() == wanted), None)


def bgr_to_uyvy(frame: np.ndarray) -> np.ndarray:
    """Convert a BGR frame to packed UYVY (``2vuy``), the format the sink takes.

    OpenCV is already a dependency, so this needs no libyuv. UYVY subsamples
    chroma horizontally, so the width must be even — odd widths are cropped by a
    column rather than refused, since dropping one pixel beats dropping a frame.
    """
    if frame.shape[1] % 2:
        frame = frame[:, :-1]
    return cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_UYVY)


# --- CoreMediaIO access (macOS only) --------------------------------------


class CoreMediaIOUnavailable(RuntimeError):
    """CoreMediaIO could not be loaded (wrong OS, or frameworks missing)."""


class _Frameworks:
    """Lazily-loaded CoreMediaIO + CoreFoundation handles."""

    def __init__(self) -> None:
        if not sys.platform.startswith("darwin"):
            raise CoreMediaIOUnavailable("CoreMediaIO is macOS-only")
        try:
            self.cmio = ctypes.CDLL(_CMIO_FRAMEWORK)
            self.cf = ctypes.CDLL(_CF_FRAMEWORK)
        except OSError as exc:  # pragma: no cover - a macOS without the framework
            raise CoreMediaIOUnavailable(str(exc)) from exc

        self.cmio.CMIOObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_PropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.cmio.CMIOObjectGetPropertyData.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_PropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self.cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            ctypes.c_uint32,
        ]

    # -- property readers ---------------------------------------------

    def _address(self, selector: int) -> _PropertyAddress:
        return _PropertyAddress(selector, _SCOPE_GLOBAL, 0)

    def uint32_array(self, obj: int, selector: int) -> list[int]:
        address = self._address(selector)
        size = ctypes.c_uint32(0)
        if self.cmio.CMIOObjectGetPropertyDataSize(
            obj, ctypes.byref(address), 0, None, ctypes.byref(size)
        ):
            return []
        count = size.value // ctypes.sizeof(ctypes.c_uint32)
        if count == 0:
            return []
        buffer = (ctypes.c_uint32 * count)()
        used = ctypes.c_uint32(0)
        if self.cmio.CMIOObjectGetPropertyData(
            obj, ctypes.byref(address), 0, None, size.value, ctypes.byref(used), buffer
        ):
            return []
        return list(buffer)

    def uint32(self, obj: int, selector: int) -> int | None:
        address = self._address(selector)
        value = ctypes.c_uint32(0)
        used = ctypes.c_uint32(0)
        if self.cmio.CMIOObjectGetPropertyData(
            obj, ctypes.byref(address), 0, None, 4, ctypes.byref(used), ctypes.byref(value)
        ):
            return None
        return value.value

    def string(self, obj: int, selector: int) -> str | None:
        address = self._address(selector)
        ref = ctypes.c_void_p()
        used = ctypes.c_uint32(0)
        if self.cmio.CMIOObjectGetPropertyData(
            obj,
            ctypes.byref(address),
            0,
            None,
            ctypes.sizeof(ctypes.c_void_p),
            ctypes.byref(used),
            ctypes.byref(ref),
        ):
            return None
        if not ref:
            return None
        buffer = ctypes.create_string_buffer(512)
        if not self.cf.CFStringGetCString(ref, buffer, 512, _CF_STRING_ENCODING_UTF8):
            return None
        return buffer.value.decode("utf-8", "replace")


def enumerate_devices(frameworks: _Frameworks | None = None) -> list[CmioDevice]:
    """Every CoreMediaIO device on this Mac, with its streams and directions."""
    api = frameworks or _Frameworks()
    devices: list[CmioDevice] = []
    for device_id in api.uint32_array(_SYSTEM_OBJECT, _PROP_DEVICES):
        streams = tuple(
            CmioStream(stream_id=sid, direction=api.uint32(sid, _PROP_STREAM_DIRECTION))
            for sid in api.uint32_array(device_id, _PROP_STREAMS)
        )
        devices.append(
            CmioDevice(
                device_id=device_id,
                uid=api.string(device_id, _PROP_DEVICE_UID),
                streams=streams,
            )
        )
    return devices


#: Told to the operator when OBS's camera extension isn't usable. Actionable on
#: purpose — "not found" alone sends people hunting in the wrong place, since the
#: usual cause is an installed-but-unapproved extension.
OBS_CAMERA_MISSING = (
    "OBS Virtual Camera not found — install OBS, then approve its camera "
    "extension in System Settings → General → Login Items & Extensions"
)


def find_obs_sink() -> tuple[CmioDevice, CmioStream] | None:
    """Locate OBS's virtual-camera device and its sink stream, if available."""
    try:
        devices = enumerate_devices()
    except CoreMediaIOUnavailable:
        return None
    device = find_obs_device(devices)
    if device is None:
        return None
    stream = select_sink_stream(device.streams)
    return None if stream is None else (device, stream)
