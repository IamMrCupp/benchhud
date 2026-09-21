"""Thermal camera capture: raw Y16 in, per-pixel °C out.

The thermal panel and compositor need **temperatures**, not a picture. The
Sipeed T256s provides them through its raw ``Y16`` stream: 16-bit pixels
holding Kelvin x 64 (see :mod:`benchhud.thermal.convert`). Its MJPEG stream is
already colour-mapped and can't be turned back into temperatures, and neither
can an ordinary webcam.

So :class:`Y16Camera` asks the driver for ``Y16`` with RGB conversion off, then
**checks the first frame** rather than trusting that the request worked.
OpenCV doesn't fail when a backend ignores the format request: AVFoundation on
a Mac simply hands back 8-bit BGR. Anything other than single-channel 16-bit
data sets :attr:`fatal_error`, a reason
:class:`~benchhud.capture.fallback.FallbackFrameSource` shows and doesn't
retry. Reading an 8-bit image as °C would make up temperatures, and the panel
never shows a made-up reading.

Y16 is a V4L2 (Linux) path. On macOS and Windows no UVC thermal camera is
known to expose raw Y16 through OpenCV, so on those platforms this is expected
to report "not a Y16 thermal camera". That's honest, not a regression.

The capture object is built by an injected factory, so every path is
testable without a camera.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from benchhud.capture.camera import Capture, parse_device
from benchhud.thermal.convert import y16_to_celsius

# cv2 property ids, inlined like capture.camera so importing needs no cv2.
_CAP_PROP_FRAME_WIDTH = 3
_CAP_PROP_FRAME_HEIGHT = 4
_CAP_PROP_FOURCC = 6
_CAP_PROP_CONVERT_RGB = 16
_CAP_V4L2 = 200

#: ``'Y16 '``: the trailing space is part of the fourcc (research.md §4).
Y16_FOURCC = int.from_bytes(b"Y16 ", "little")

#: The Sipeed's native Y16 mode. 480x640 is upscaled, with no extra detail.
Y16_SIZE = (192, 256)

NOT_Y16 = "isn't a Y16 thermal camera (no raw temperature stream)"


class ThermalCapture(Capture, Protocol):
    """A :class:`Capture` that can also be asked for a pixel format."""

    def set(self, prop_id: int, value: float) -> bool: ...


ThermalCaptureFactory = Callable[[int | str], ThermalCapture]


def _default_factory(device: int | str) -> ThermalCapture:
    import cv2

    if isinstance(device, str) and sys.platform.startswith("linux"):
        return cv2.VideoCapture(device, _CAP_V4L2)
    return cv2.VideoCapture(device)


def raw_y16(frame: Any, size: tuple[int, int]) -> np.ndarray | None:
    """A frame as a ``(H, W)`` uint16 array, or ``None`` if it isn't Y16 data.

    OpenCV hands Y16 back either as a ready ``uint16`` image or, on some builds,
    as a flat byte buffer that has to be reinterpreted as little-endian.
    """
    if not isinstance(frame, np.ndarray):
        return None
    if frame.dtype == np.uint16:
        image = frame[:, :, 0] if frame.ndim == 3 and frame.shape[2] == 1 else frame
        return image if image.ndim == 2 else None
    width, height = size
    if frame.dtype == np.uint8 and width > 0 and height > 0 and frame.size == width * height * 2:
        # Only a buffer with exactly 2 bytes per pixel. A 3-channel BGR frame
        # has 3 and is rejected.
        if frame.ndim == 3 and frame.shape[2] == 3:
            return None
        return np.ascontiguousarray(frame).view("<u2").reshape(height, width)
    return None


class Y16Camera:
    """A thermal camera read as per-pixel °C (a ``FallbackFrameSource`` camera)."""

    def __init__(
        self,
        device: int | str,
        *,
        capture_factory: ThermalCaptureFactory | None = None,
    ) -> None:
        self._device = parse_device(device)
        self._factory = capture_factory or _default_factory
        self._capture: ThermalCapture | None = None
        self._size = (0, 0)
        self.fatal_error: str | None = None
        self.open()

    @property
    def device(self) -> int | str:
        return self._device

    @property
    def is_open(self) -> bool:
        return self._capture is not None

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def open(self) -> bool:
        self.release()
        try:
            capture = self._factory(self._device)
        except Exception:
            return False
        if not capture.isOpened():
            capture.release()
            return False
        # Ask for raw Y16. Setting a property the backend ignores isn't an
        # error, so the first frame is the real test (see read()).
        capture.set(_CAP_PROP_FOURCC, Y16_FOURCC)
        capture.set(_CAP_PROP_FRAME_WIDTH, Y16_SIZE[0])
        capture.set(_CAP_PROP_FRAME_HEIGHT, Y16_SIZE[1])
        capture.set(_CAP_PROP_CONVERT_RGB, 0)
        self._capture = capture
        self._size = (
            int(capture.get(_CAP_PROP_FRAME_WIDTH)),
            int(capture.get(_CAP_PROP_FRAME_HEIGHT)),
        )
        return True

    def read(self) -> np.ndarray | None:
        """The next frame as a float32 °C array, or ``None``."""
        if self._capture is None:
            return None
        try:
            ok, frame = self._capture.read()
        except Exception:
            return None
        if not ok or frame is None:
            return None
        raw = raw_y16(frame, self._size)
        if raw is None:
            self.fatal_error = NOT_Y16
            self.release()
            return None
        self._size = (raw.shape[1], raw.shape[0])
        return y16_to_celsius(raw)

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def close(self) -> None:
        self.release()
