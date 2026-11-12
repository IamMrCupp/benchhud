"""Camera-backed :class:`~benchhud.capture.frame_source.FrameSource`.

Wraps ``cv2.VideoCapture``, which takes either an integer index (macOS/Windows)
or a device path (``/dev/video4`` on Linux) — :func:`parse_device` accepts both
so the same config string works everywhere.

Opening is **lazy and forgiving**: a camera that isn't plugged in yet must not
crash the shell, so the constructor never raises and :meth:`read` returns
``None`` until the device shows up. :attr:`is_open` and :attr:`last_error` say
why, for the status bar.

The capture object is created via an injected factory so tests exercise every
path — closed device, open-but-no-frame, resolution reporting — with no camera.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

# cv2 property ids, inlined so this module imports with no camera and no cv2 at
# import time (cv2 is only touched inside the default factory).
_CAP_PROP_FRAME_WIDTH = 3
_CAP_PROP_FRAME_HEIGHT = 4
_CAP_PROP_FOURCC = 6
_FOURCC_MJPG = 0x47504A4D  # "MJPG", little-endian


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


def _default_factory(device: int | str) -> Capture:
    import cv2

    return cv2.VideoCapture(device)


class CameraFrameSource:
    """A :class:`FrameSource` backed by a real camera."""

    def __init__(
        self,
        device: int | str = 0,
        *,
        size: tuple[int, int] | None = None,
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
        self._size = (
            int(capture.get(_CAP_PROP_FRAME_WIDTH)),
            int(capture.get(_CAP_PROP_FRAME_HEIGHT)),
        )
        self.size_note = None
        if self._requested is not None and self._size != self._requested:
            want, got = self._requested, self._size
            self.size_note = f"asked for {want[0]}x{want[1]}, the camera gave {got[0]}x{got[1]}"
        return True

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

        self._last_error = None
        # Trust the frame over the driver's advertised resolution: some UVC
        # cameras report 0x0 until the first successful grab.
        self._size = (frame.shape[1], frame.shape[0])
        self._last_frame = frame
        self.decodes += 1
        return frame
