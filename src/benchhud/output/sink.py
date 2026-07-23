"""Output sinks for the composited frame.

Consumers depend on the :class:`OutputSink` protocol. :class:`FakeSink` records
frames in memory for tests; :class:`~benchhud.output.v4l2.V4l2LoopbackSink`
writes them to a v4l2loopback device for OBS to capture.

Virtual-camera output is Linux-only today. macOS and Windows are P2 portability
targets and their backends need a non-GPL path — see ``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import numpy as np

from benchhud.output.v4l2 import V4l2LoopbackSink

log = logging.getLogger(__name__)


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


class NullSink:
    """:class:`OutputSink` that discards frames, keeping only a count.

    The fallback when virtual-camera output can't be opened: it runs for the
    life of the app, so unlike :class:`FakeSink` it must not retain frames —
    at 1280x720x15fps that would be ~41 MB/s of leak.
    """

    def __init__(self, width: int = 1280, height: int = 720) -> None:
        self._size = (width, height)
        self.frame_count = 0
        self.closed = False

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def send(self, frame: np.ndarray) -> None:
        self.frame_count += 1

    def close(self) -> None:
        self.closed = True


def open_output_sink(device: str, width: int, height: int) -> OutputSink:
    """Open the loopback output, falling back to a discarding sink.

    A missing ``v4l2loopback`` module, a wrong device path, or another writer
    already holding the node must leave the HUD usable — the bench still works
    without the OBS feed — so we log and fall back rather than fail.
    """
    try:
        return V4l2LoopbackSink(device, width, height)
    except (OSError, RuntimeError) as exc:
        log.warning("virtual-camera output unavailable on %s: %s", device, exc)
        return NullSink(width, height)
