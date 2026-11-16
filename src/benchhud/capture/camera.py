"""Camera-backed :class:`~benchhud.capture.frame_source.FrameSource`.

Wraps ``cv2.VideoCapture``, which takes either an integer index (macOS/Windows)
or a device path (``/dev/video4`` on Linux) — :func:`parse_device` accepts both
so the same config string works everywhere.

Opening is **lazy and forgiving**: a camera that isn't plugged in yet must not
crash the shell, so the constructor never raises and :meth:`read` returns
``None`` until the device shows up. :attr:`is_open` and :attr:`last_error` say
why, for the status bar.

On Linux a ``/dev/...`` path is opened with OpenCV's V4L2 backend (#377). Left
to choose, OpenCV 5 opens ``/dev/video4`` with FFmpeg, which refuses the size
and format requests and runs the camera in whatever mode it was left in.
:meth:`open` and :meth:`read` check what the camera really sends and log it
when it isn't what was asked for.

The capture object is created via an injected factory so tests exercise every
path — closed device, open-but-no-frame, resolution reporting — with no camera.

With ``reduced_live`` an MJPEG camera is read as compressed frames and decoded
here at a fraction of its size (#349): libjpeg scales inside the decode, which
on the bench costs about 8 ms for a 1080p frame against 28 ms for the full one.
:meth:`read` returns the small frame, :attr:`size` stays the camera's, and
:meth:`snapshot` decodes the same frame at full size when someone saves it.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

# cv2 property ids, inlined so this module imports with no camera and no cv2 at
# import time (cv2 is only touched inside the default factory).
_CAP_PROP_FRAME_WIDTH = 3
_CAP_PROP_FRAME_HEIGHT = 4
_CAP_PROP_FOURCC = 6
_CAP_PROP_FORMAT = 8
_CAP_PROP_CONVERT_RGB = 16
_CAP_V4L2 = 200
_FOURCC_MJPG = 0x47504A4D  # "MJPG", little-endian

_log = logging.getLogger(__name__)

#: The live frame is never decoded narrower than this: the test pattern's
#: width, and half of 1080p.
LIVE_MIN_WIDTH = 960
_JPEG_MAGIC = b"\xff\xd8"


def live_reduction(width: int, min_width: int = LIVE_MIN_WIDTH) -> int:
    """How far to shrink a ``width``-wide frame in the decode: 1, 2, 4 or 8.

    The largest of libjpeg's scales that still leaves ``min_width`` across, so
    1080p and 4K both come out 960 wide and 720p is left alone.
    """
    for factor in (8, 4, 2):
        if width >= min_width * factor:
            return factor
    return 1


def full_size_of(frame: np.ndarray, source: Any) -> tuple[int, int]:
    """The camera's own ``(width, height)`` for a frame ``source`` gave out.

    A reduced live frame (#349) is smaller than the camera's; everything that
    works in scope pixels (the thermal registration, calibration clicks) means
    the camera's. A frame that isn't a clean 1/2, 1/4 or 1/8 of the source's
    size (the test pattern, a frame from before a resolution change) is its
    own size.
    """
    height, width = frame.shape[:2]
    full_w, full_h = getattr(source, "size", (0, 0)) or (0, 0)
    for factor in (2, 4, 8):
        if (-(-full_w // factor), -(-full_h // factor)) == (width, height):
            return (full_w, full_h)
    return (width, height)


class Capture(Protocol):
    """The slice of ``cv2.VideoCapture`` we depend on."""

    def isOpened(self) -> bool: ...  # cv2's spelling

    def read(self) -> tuple[bool, Any]: ...

    # grab() / retrieve() are used when present, to decode on demand (#346).

    def get(self, prop_id: int) -> float: ...

    def set(self, prop_id: int, value: float) -> bool: ...

    def release(self) -> None: ...


CaptureFactory = Callable[[int | str], Capture]


def parse_device(device: int | str) -> int | str:
    """Normalize a configured device: ``"0"`` → ``0``, ``/dev/video4`` unchanged.

    OpenCV treats an integer as a camera index and a string as a path/URL, and
    the two are not interchangeable — a bare ``"0"`` opens nothing.
    """
    if isinstance(device, int):
        return device
    text = device.strip()
    return int(text) if text.lstrip("-").isdigit() else text


def open_arguments(device: int | str, platform: str | None = None) -> tuple[int | str, ...]:
    """What to call ``cv2.VideoCapture`` with for ``device`` on ``platform``.

    A Linux device path names the V4L2 backend (#377): OpenCV's own pick for
    ``/dev/video4`` is FFmpeg, which ignores every ``set()``. Indexes, and
    everything on macOS and Windows, stay with OpenCV's choice.
    """
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux") and isinstance(device, str) and device.startswith("/dev/"):
        return (device, _CAP_V4L2)
    return (device,)


def fourcc_text(value: float) -> str:
    """A cv2 fourcc property as text: ``"MJPG"``, or ``"?"`` when it isn't one."""
    try:
        text = int(value).to_bytes(4, "little").decode("ascii").strip()
    except (OverflowError, ValueError):  # negative, too big, or not ASCII
        return "?"
    return text if text.isprintable() and text else "?"


def _default_factory(device: int | str) -> Capture:
    import cv2

    return cv2.VideoCapture(*open_arguments(device))


class CameraFrameSource:
    """A :class:`FrameSource` backed by a real camera."""

    def __init__(
        self,
        device: int | str = 0,
        *,
        size: tuple[int, int] | None = None,
        reduced_live: bool = False,
        capture_factory: CaptureFactory | None = None,
    ) -> None:
        self._device = parse_device(device)
        # The resolution to ask for (#247); None takes the camera's default.
        self._requested = size if size and size[0] > 0 and size[1] > 0 else None
        self.size_note: str | None = None
        self._factory = capture_factory or _default_factory
        self._capture: Capture | None = None
        self._size = (0, 0)
        self._last_error: str | None = None
        self._closed = False
        # Decode on demand (#346): see set_decode_gate.
        self._decode_gate: Callable[[], bool] | None = None
        self._last_frame: np.ndarray | None = None
        # Reduced live decode (#349): wanted, and whether this capture is
        # handing over compressed frames. _last_jpeg is the frame behind
        # _last_frame, kept so a snapshot can decode it at full size.
        self._reduced_live = reduced_live
        self._compressed = False
        self._last_jpeg: np.ndarray | None = None
        #: Frames actually decoded, for measuring what the gate saves.
        self.decodes = 0
        self.open()

    # --- lifecycle -------------------------------------------------------

    def open(self) -> bool:
        """(Re)open the device. Never raises — check the return value."""
        self.release()
        self._closed = False
        try:
            capture = self._factory(self._device)
        except Exception as exc:  # a bad path/backend must not kill the shell
            self._last_error = f"could not open {self._device}: {exc}"
            return False
        if not capture.isOpened():
            capture.release()
            self._last_error = f"could not open {self._device}"
            return False

        self._capture = capture
        self._last_error = None
        if self._requested is not None:
            self._request_size(capture, self._requested)
        self.size_note = None
        self._delivered(
            (int(capture.get(_CAP_PROP_FRAME_WIDTH)), int(capture.get(_CAP_PROP_FRAME_HEIGHT)))
        )
        self._compressed = self._reduced_live and self._request_compressed(capture)
        backend = getattr(capture, "getBackendName", None)
        _log.info(
            "camera %s opened: %dx%d %s, backend %s%s",
            self._device,
            *self._size,
            fourcc_text(capture.get(_CAP_PROP_FOURCC)),
            backend() if backend is not None else "?",
            ", compressed frames" if self._compressed else "",
        )
        return True

    def _delivered(self, size: tuple[int, int]) -> None:
        """Record the size the camera is sending, and say when it isn't the one asked for.

        Called with the driver's answer at open and again with the first frame
        that disagrees with it: the frame is what the camera really delivered.
        A 0x0 answer is a driver that doesn't know yet, not a refusal.
        """
        self._size = size
        note = None
        if self._requested is not None and size != self._requested and size != (0, 0):
            want = self._requested
            note = f"asked for {want[0]}x{want[1]}, the camera gave {size[0]}x{size[1]}"
            if note != self.size_note:
                _log.warning("camera %s: %s", self._device, note)
        self.size_note = note

    @staticmethod
    def _request_compressed(capture: Capture) -> bool:
        """Ask an MJPEG camera for its frames still compressed (#349).

        OpenCV's FFmpeg backend does it with ``FORMAT = -1`` and its V4L2
        backend with ``CONVERT_RGB = 0``. FFmpeg also accepts the second and
        then sends a grey plane, so the order matters. :meth:`read` checks that
        what arrives really is a JPEG.
        """
        setter = getattr(capture, "set", None)
        if setter is None or int(capture.get(_CAP_PROP_FOURCC)) != _FOURCC_MJPG:
            return False
        return bool(setter(_CAP_PROP_FORMAT, -1) or setter(_CAP_PROP_CONVERT_RGB, 0))

    @staticmethod
    def _request_size(capture: Capture, size: tuple[int, int]) -> None:
        """MJPG first: it's what reaches 1080p and 4K over USB 2."""
        setter = getattr(capture, "set", None)
        if setter is None:
            return
        setter(_CAP_PROP_FOURCC, _FOURCC_MJPG)
        setter(_CAP_PROP_FRAME_WIDTH, size[0])
        setter(_CAP_PROP_FRAME_HEIGHT, size[1])

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._last_frame = None  # a reopened camera starts with a real decode
        self._last_jpeg = None
        self._compressed = False

    def close(self) -> None:
        self.release()
        self._closed = True

    # --- state -----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._capture is not None

    @property
    def last_error(self) -> str | None:
        """Why the last open/read failed, or ``None`` when healthy."""
        return self._last_error

    @property
    def device(self) -> int | str:
        return self._device

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    # --- frames ----------------------------------------------------------

    def set_decode_gate(self, gate: Callable[[], bool] | None) -> None:
        """Decode a grabbed frame only when ``gate()`` says someone wants one (#346).

        The camera still delivers every frame and each is still grabbed (that
        keeps the driver's queue drained and the picture current), but decoding
        a 1080p MJPEG frame costs ~19 ms. When the HUD renders slower than the
        camera sends, the frames nobody takes aren't decoded; :meth:`read`
        hands back the last decoded frame for them instead. ``None`` decodes
        every frame, as before.
        """
        self._decode_gate = gate

    def read(self) -> np.ndarray | None:
        """Grab the next BGR frame, or ``None`` on any failure."""
        if self._closed or self._capture is None:
            return None
        gate = self._decode_gate
        try:
            if gate is not None and self._last_frame is not None and hasattr(self._capture, "grab"):
                if not self._capture.grab():
                    ok, frame = False, None
                elif gate():
                    ok, frame = self._capture.retrieve()
                else:
                    return self._last_frame  # grabbed, not wanted: skip the decode
            else:
                ok, frame = self._capture.read()
        except Exception as exc:
            self._last_error = f"read failed: {exc}"
            return None
        if not ok or frame is None:
            # A dropped frame is normal (USB hiccup, NUC shutter on the thermal
            # cam) — report it but stay open so the next read can recover.
            self._last_error = "no frame"
            return None

        if self._compressed:
            return self._decode_live(frame)
        self._last_error = None
        # Trust the frame over the driver's advertised resolution: some UVC
        # cameras report 0x0 until the first successful grab.
        if (frame.shape[1], frame.shape[0]) != self._size:
            self._delivered((frame.shape[1], frame.shape[0]))
        self._last_frame = frame
        self.decodes += 1
        return frame

    def _decode_live(self, buffer: np.ndarray) -> np.ndarray | None:
        """Decode a compressed frame at the live size, keeping it for a snapshot."""
        import cv2

        data = np.ascontiguousarray(buffer).reshape(-1)
        if data.dtype != np.uint8 or bytes(data[:2]) != _JPEG_MAGIC:
            # Not a JPEG after all (a backend that took the request and sends
            # something else): go back to OpenCV's own decode for good.
            self._reduced_live = False
            self.open()
            self._last_error = "no frame"
            return None
        factor = live_reduction(self._size[0])
        flags = {
            1: cv2.IMREAD_COLOR,
            2: cv2.IMREAD_REDUCED_COLOR_2,
            4: cv2.IMREAD_REDUCED_COLOR_4,
            8: cv2.IMREAD_REDUCED_COLOR_8,
        }
        frame = cv2.imdecode(data, flags[factor])
        if frame is None:  # a torn frame off USB: a missed frame, not a lost camera
            self._last_error = "no frame"
            return None
        self._last_error = None
        height, width = frame.shape[:2]
        if factor == 1:
            if (width, height) != self._size:
                self._delivered((width, height))
        elif (-(-self._size[0] // factor), -(-self._size[1] // factor)) != (width, height):
            # The driver's advertised size was wrong; learn it from a full decode.
            full = cv2.imdecode(data, cv2.IMREAD_COLOR)
            self._delivered((full.shape[1], full.shape[0]))
        self._last_jpeg = data
        self._last_frame = frame
        self.decodes += 1
        return frame

    def snapshot(self) -> np.ndarray | None:
        """The frame :meth:`read` last gave out, at the camera's full size.

        Safe from another thread than the one reading: it decodes the kept
        compressed frame and doesn't touch the device. ``None`` before the
        first frame.
        """
        data, frame = self._last_jpeg, self._last_frame
        if data is None:
            return frame  # not reducing: the live frame is already full size
        import cv2

        full = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return full if full is not None else frame
