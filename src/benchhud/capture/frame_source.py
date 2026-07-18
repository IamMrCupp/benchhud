"""Frame sources.

Everything downstream (the latest-frame reader, the scope panel, the compositor)
depends on the :class:`FrameSource` protocol, never on a concrete camera. That
keeps the whole pipeline testable off-hardware via :class:`SyntheticFrameSource`,
and lets the device-backed source (``cv2.VideoCapture``, bench-gated) drop in
without touching consumers.

Frames are ``numpy`` arrays in OpenCV's native **BGR**, shape ``(H, W, 3)``,
dtype ``uint8``. ``read()`` returns ``None`` when no frame is available (source
not ready, or transient miss) — callers must tolerate that.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class FrameSource(Protocol):
    """A source of BGR video frames."""

    @property
    def size(self) -> tuple[int, int]:
        """``(width, height)`` in pixels."""
        ...

    def read(self) -> np.ndarray | None:
        """Return the next BGR frame, or ``None`` if none is available."""
        ...

    def close(self) -> None:
        """Release any underlying resource."""
        ...


class SyntheticFrameSource:
    """A deterministic, hardware-free :class:`FrameSource`.

    Produces frames with a moving vertical bar over a fixed background so tests
    can distinguish successive frames. ``frame_index`` advances on each ``read``.
    Useful both in tests and as a "no camera attached" dev fallback.
    """

    def __init__(self, width: int = 640, height: int = 480) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        self._width = width
        self._height = height
        self.frame_index = 0
        self._closed = False

    @property
    def size(self) -> tuple[int, int]:
        return (self._width, self._height)

    def read(self) -> np.ndarray | None:
        if self._closed:
            return None
        frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        # Dim background so the frame is never all-black (helps visual debugging).
        frame[:, :, 0] = 32  # B
        # A bright bar that sweeps left→right, one column per frame.
        bar_x = self.frame_index % self._width
        frame[:, bar_x, :] = 255
        self.frame_index += 1
        return frame

    def close(self) -> None:
        self._closed = True
