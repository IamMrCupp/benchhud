"""Output sinks for the composited frame.

Consumers depend on the :class:`OutputSink` protocol. :class:`FakeSink` records
frames in memory for tests; :class:`VirtualCamSink` pushes to a virtual camera
via ``pyvirtualcam`` (v4l2loopback on Linux, OBS virtual cam on macOS/Windows).

The virtual-cam *device* can only be opened on a machine with the backend
installed (the Linux bench), so :class:`VirtualCamSink` takes an injectable
``camera_factory`` — the BGR→RGB conversion is unit-tested with a fake camera,
and the real device path is exercised on the bench.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class OutputSink(Protocol):
    """A destination for BGR frames."""

    @property
    def size(self) -> tuple[int, int]:
        """``(width, height)`` the sink expects."""
        ...

    def send(self, frame: np.ndarray) -> None:
        """Push one BGR frame to the sink."""
        ...

    def close(self) -> None:
        """Release the sink."""
        ...


class FakeSink:
    """In-memory :class:`OutputSink` that records frames sent to it."""

    def __init__(self, width: int = 640, height: int = 480) -> None:
        self._size = (width, height)
        self.frames: list[np.ndarray] = []
        self.closed = False

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def send(self, frame: np.ndarray) -> None:
        if self.closed:
            raise RuntimeError("sink is closed")
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True


# A camera factory takes (width, height, fps) and returns an object exposing
# ``send(rgb_frame)`` and ``close()`` — pyvirtualcam.Camera satisfies this.
CameraFactory = Callable[[int, int, int], object]


class VirtualCamSink:
    """Push frames to a virtual camera (pyvirtualcam / v4l2loopback).

    ``pyvirtualcam`` expects RGB; OpenCV frames are BGR, so we swap channels on
    the way out. Opening the device requires the platform backend, so the real
    factory is only usable on the bench; tests inject a fake camera.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fps: int = 15,
        *,
        camera_factory: CameraFactory | None = None,
    ) -> None:
        self._size = (width, height)
        if camera_factory is None:
            camera_factory = _default_camera_factory
        self._camera = camera_factory(width, height, fps)

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def send(self, frame: np.ndarray) -> None:
        rgb = np.ascontiguousarray(frame[:, :, ::-1])  # BGR -> RGB
        self._camera.send(rgb)

    def close(self) -> None:
        self._camera.close()


def _default_camera_factory(width: int, height: int, fps: int) -> object:
    """Open a real ``pyvirtualcam.Camera`` (needs a platform backend)."""
    import pyvirtualcam

    return pyvirtualcam.Camera(width=width, height=height, fps=fps)
