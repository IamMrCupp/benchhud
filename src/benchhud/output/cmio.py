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
import logging
import sys
from dataclasses import dataclass, field

import cv2
import numpy as np

from benchhud.devices.discovery import OBS_VIRTUAL_CAMERA_UID

log = logging.getLogger(__name__)

# OBS's camera-extension device, from its Info.plist (OBSCameraDeviceUUID).
# Stable since the extension shipped; verified against OBS 32.1.2.
OBS_DEVICE_UID = OBS_VIRTUAL_CAMERA_UID

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


# --- frame delivery (macOS only) ------------------------------------------
#
# The pixel path — CVPixelBuffer → CMSampleBuffer → the sink's CMSimpleQueue —
# lives in CoreMedia/CoreVideo, not CoreMediaIO. It was verified end to end on a
# real Mac: with a non-NULL altered-callback the extension vended a queue and
# dequeued every one of 300 pushed frames (that callback firing is OBS's
# extension consuming our frames), with stable memory. The lifetime rules below
# come from that run, not from docs.
#
# Dequeued isn't delivered, though (#177). The extension runs in another
# process, and a sample only crosses the XPC boundary as an **IOSurface**. A
# pixel buffer created with no attributes lives in plain heap memory. It
# enqueues and dequeues fine, then CoreMediaIO drops it with "no iosurface", so
# OBS never sees a picture. Every buffer is therefore created with
# ``kCVPixelBufferIOSurfacePropertiesKey: {}``.

_CM_FRAMEWORK = "/System/Library/Frameworks/CoreMedia.framework/CoreMedia"
_CV_FRAMEWORK = "/System/Library/Frameworks/CoreVideo.framework/CoreVideo"

#: kCVPixelFormatType_422YpCbCr8 — ``'2vuy'`` (packed UYVY), what the sink takes.
_PIXEL_FORMAT_UYVY = 0x32767579
_TIMESCALE = 600  # CMTime ticks per second; fine granularity for live timing


class _CMTime(ctypes.Structure):
    _fields_ = [
        ("value", ctypes.c_int64),
        ("timescale", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
        ("epoch", ctypes.c_int64),
    ]


class _CMSampleTimingInfo(ctypes.Structure):
    _fields_ = [
        ("duration", _CMTime),
        ("presentationTimeStamp", _CMTime),
        ("decodeTimeStamp", _CMTime),
    ]


#: void(CMIOStreamID, void* token, void* refcon) — fired when the extension
#: dequeues, signalling room to enqueue more. Kept as a module type so the bound
#: instance can be retained (letting Python GC it crashes the CMIO thread).
_QueueAlteredProc = ctypes.CFUNCTYPE(None, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)


class _DeliveryFrameworks:
    """CoreMedia + CoreVideo + CoreMediaIO calls for pushing frames (macOS)."""

    def __init__(self) -> None:
        if not sys.platform.startswith("darwin"):
            raise CoreMediaIOUnavailable("CoreMediaIO is macOS-only")
        try:
            self.cm = ctypes.CDLL(_CM_FRAMEWORK)
            self.cv = ctypes.CDLL(_CV_FRAMEWORK)
            self.cmio = ctypes.CDLL(_CMIO_FRAMEWORK)
            self.cf = ctypes.CDLL(_CF_FRAMEWORK)
        except OSError as exc:  # pragma: no cover - a macOS without the frameworks
            raise CoreMediaIOUnavailable(str(exc)) from exc
        vp = ctypes.c_void_p
        self.cv.CVPixelBufferCreate.argtypes = [
            vp,
            ctypes.c_size_t,
            ctypes.c_size_t,
            ctypes.c_uint32,
            vp,
            ctypes.POINTER(vp),
        ]
        self.cv.CVPixelBufferCreate.restype = ctypes.c_int32
        self.cv.CVPixelBufferLockBaseAddress.argtypes = [vp, ctypes.c_uint64]
        self.cv.CVPixelBufferUnlockBaseAddress.argtypes = [vp, ctypes.c_uint64]
        self.cv.CVPixelBufferGetBaseAddress.argtypes = [vp]
        self.cv.CVPixelBufferGetBaseAddress.restype = vp
        self.cv.CVPixelBufferGetBytesPerRow.argtypes = [vp]
        self.cv.CVPixelBufferGetBytesPerRow.restype = ctypes.c_size_t
        self.cv.CVPixelBufferRelease.argtypes = [vp]
        self.cm.CMVideoFormatDescriptionCreateForImageBuffer.argtypes = [vp, vp, ctypes.POINTER(vp)]
        self.cm.CMVideoFormatDescriptionCreateForImageBuffer.restype = ctypes.c_int32
        self.cm.CMSampleBufferCreateForImageBuffer.argtypes = [
            vp,
            vp,
            ctypes.c_char,
            vp,
            vp,
            vp,
            ctypes.POINTER(_CMSampleTimingInfo),
            ctypes.POINTER(vp),
        ]
        self.cm.CMSampleBufferCreateForImageBuffer.restype = ctypes.c_int32
        self.cm.CMSimpleQueueEnqueue.argtypes = [vp, vp]
        self.cm.CMSimpleQueueEnqueue.restype = ctypes.c_int32
        self.cf.CFRelease.argtypes = [vp]
        self.cmio.CMIOStreamCopyBufferQueue.argtypes = [ctypes.c_uint32, vp, vp, ctypes.POINTER(vp)]
        self.cmio.CMIOStreamCopyBufferQueue.restype = ctypes.c_int32
        self.cmio.CMIODeviceStartStream.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.cmio.CMIODeviceStartStream.restype = ctypes.c_int32
        self.cmio.CMIODeviceStopStream.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        self.cv.CVPixelBufferGetIOSurface.argtypes = [vp]
        self.cv.CVPixelBufferGetIOSurface.restype = vp
        self.cf.CFDictionaryCreate.argtypes = [
            vp,
            ctypes.POINTER(vp),
            ctypes.POINTER(vp),
            ctypes.c_long,
            vp,
            vp,
        ]
        self.cf.CFDictionaryCreate.restype = vp
        self.pixel_buffer_attributes = self._iosurface_attributes()

    def _iosurface_attributes(self) -> ctypes.c_void_p:
        """``{kCVPixelBufferIOSurfacePropertiesKey: {}}``, built once and kept.

        An empty properties dict means "back this buffer with an IOSurface, with
        default properties", which is what lets it cross into the extension.
        """
        vp = ctypes.c_void_p
        key_callbacks = ctypes.addressof(
            ctypes.c_char.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks")
        )
        value_callbacks = ctypes.addressof(
            ctypes.c_char.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks")
        )
        empty = self.cf.CFDictionaryCreate(None, None, None, 0, key_callbacks, value_callbacks)
        key = vp.in_dll(self.cv, "kCVPixelBufferIOSurfacePropertiesKey")
        keys = (vp * 1)(key.value)
        values = (vp * 1)(empty)
        attributes = self.cf.CFDictionaryCreate(
            None, keys, values, 1, key_callbacks, value_callbacks
        )
        self.cf.CFRelease(empty)  # the outer dict retains it
        if not attributes:
            raise CoreMediaIOUnavailable("could not build IOSurface pixel-buffer attributes")
        return vp(attributes)

    def new_pixel_buffer(self, width: int, height: int) -> ctypes.c_void_p | None:
        """An IOSurface-backed UYVY pixel buffer (caller releases), or None."""
        pixel_buffer = ctypes.c_void_p()
        if self.cv.CVPixelBufferCreate(
            None,
            width,
            height,
            _PIXEL_FORMAT_UYVY,
            self.pixel_buffer_attributes,
            ctypes.byref(pixel_buffer),
        ):
            return None
        return pixel_buffer


class CmioSink:
    """Publish BGR frames into OBS's virtual camera. Implements ``OutputSink``.

    Frames that arrive faster than the extension drains them are dropped rather
    than blocking the GUI thread — a dropped frame beats a stalled HUD.
    """

    def __init__(
        self,
        width: int,
        height: int,
        device_id: int,
        stream_id: int,
        frameworks: _DeliveryFrameworks | None = None,
    ) -> None:
        self._size = (width, height)
        self._api = frameworks or _DeliveryFrameworks()
        self._device_id = device_id
        self._stream_id = stream_id
        self._frame = 0
        self._closed = False
        # Retain the callback for the stream's lifetime — if Python collects it,
        # the extension calls a freed pointer and the process dies.
        self._altered = _QueueAlteredProc(lambda _s, _t, _r: None)

        queue = ctypes.c_void_p()
        err = self._api.cmio.CMIOStreamCopyBufferQueue(
            stream_id, self._altered, None, ctypes.byref(queue)
        )
        if err or not queue:
            raise RuntimeError(f"CMIOStreamCopyBufferQueue failed (err={err})")
        self._queue = queue
        err = self._api.cmio.CMIODeviceStartStream(device_id, stream_id)
        if err:
            raise RuntimeError(f"CMIODeviceStartStream failed (err={err})")

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def send(self, frame: np.ndarray) -> None:
        if self._closed:
            return
        uyvy = bgr_to_uyvy(frame)
        height, width = uyvy.shape[0], uyvy.shape[1]
        api = self._api

        pixel_buffer = api.new_pixel_buffer(width, height)
        if pixel_buffer is None:
            return
        try:
            self._fill(pixel_buffer, uyvy, width, height)
            sample = self._wrap(pixel_buffer)
        finally:
            api.cv.CVPixelBufferRelease(pixel_buffer)  # the sample retains it
        if sample is None:
            return
        # On success the queue owns the sample (the consumer releases it); only
        # release it ourselves when the enqueue is rejected (queue full).
        if api.cm.CMSimpleQueueEnqueue(self._queue, sample):
            api.cf.CFRelease(sample)
        else:
            self._frame += 1

    def _fill(self, pixel_buffer, uyvy: np.ndarray, width: int, height: int) -> None:
        api = self._api
        api.cv.CVPixelBufferLockBaseAddress(pixel_buffer, 0)
        try:
            base = api.cv.CVPixelBufferGetBaseAddress(pixel_buffer)
            stride = api.cv.CVPixelBufferGetBytesPerRow(pixel_buffer)
            row_bytes = width * 2  # UYVY = 2 bytes/pixel
            contiguous = np.ascontiguousarray(uyvy)
            if stride == row_bytes:
                ctypes.memmove(base, contiguous.ctypes.data, row_bytes * height)
            else:
                # CoreVideo padded each row for alignment — copy row by row.
                src = contiguous.ctypes.data
                for row in range(height):
                    ctypes.memmove(base + row * stride, src + row * row_bytes, row_bytes)
        finally:
            api.cv.CVPixelBufferUnlockBaseAddress(pixel_buffer, 0)

    def _wrap(self, pixel_buffer) -> ctypes.c_void_p | None:
        api = self._api
        fmt = ctypes.c_void_p()
        if api.cm.CMVideoFormatDescriptionCreateForImageBuffer(
            None, pixel_buffer, ctypes.byref(fmt)
        ):
            return None
        pts = _CMTime(self._frame * _TIMESCALE // 30, _TIMESCALE, 1, 0)  # ~30fps clock
        duration = _CMTime(_TIMESCALE // 30, _TIMESCALE, 1, 0)
        timing = _CMSampleTimingInfo(duration, pts, _CMTime(0, 0, 0, 0))
        sample = ctypes.c_void_p()
        err = api.cm.CMSampleBufferCreateForImageBuffer(
            None, pixel_buffer, b"\x01", None, None, fmt, ctypes.byref(timing), ctypes.byref(sample)
        )
        api.cf.CFRelease(fmt)
        return None if err else sample

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._api.cmio.CMIODeviceStopStream(self._device_id, self._stream_id)


#: Shown when OBS's extension is present but its sink stream wouldn't start.
#: The usual cause is single-writer: OBS's own **Start Virtual Camera** already
#: holds the stream, so benchhud can't.
OBS_SINK_BUSY = (
    "OBS Virtual Camera found, but its sink stream wouldn't open — "
    "stop OBS's own Start Virtual Camera, then re-enable output"
)


class CmioSinkUnavailable(RuntimeError):
    """OBS's virtual camera can't be published to right now.

    ``reason`` is operator-facing — it names what to go fix, and it's what the
    shell puts in the status bar. Two distinct situations get two distinct
    reasons, because they need different fixes: OBS's extension isn't installed
    or approved (:data:`OBS_CAMERA_MISSING`), or it *is* there but the stream
    wouldn't open (:data:`OBS_SINK_BUSY`).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def open_cmio_sink(width: int, height: int) -> CmioSink:
    """Open a sink into OBS's virtual camera.

    Raises :class:`CmioSinkUnavailable` — never a bare framework error — with a
    reason the operator can act on. Callers that must not raise catch it and
    fall back; the reason travels with the exception rather than being
    re-derived after the fact.
    """
    found = find_obs_sink()
    if found is None:
        raise CmioSinkUnavailable(OBS_CAMERA_MISSING)
    device, stream = found
    try:
        return CmioSink(width, height, device.device_id, stream.stream_id)
    except (RuntimeError, CoreMediaIOUnavailable) as exc:
        log.warning("could not open OBS virtual-camera sink: %s", exc)
        raise CmioSinkUnavailable(f"{OBS_SINK_BUSY} ({exc})") from exc
