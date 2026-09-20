"""Output sinks for the composited frame.

Consumers depend on the :class:`OutputSink` protocol. :class:`FakeSink` records
frames in memory for tests; :class:`~benchhud.output.v4l2.V4l2LoopbackSink`
writes them to a v4l2loopback device for OBS to capture.

One real backend per platform, chosen by :func:`open_output_sink`: v4l2loopback
on Linux, OBS's camera extension on macOS (:mod:`benchhud.output.cmio`). Windows
has none yet. Where a backend can't open, the fallback :class:`NullSink` carries
a ``reason`` the shell shows — see ``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

import logging
import sys
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

    def __init__(self, width: int = 1280, height: int = 720, reason: str = "") -> None:
        self._size = (width, height)
        self.frame_count = 0
        self.closed = False
        #: Why nothing is actually being published — shown in the UI so the
        #: operator is never told output is on while frames go nowhere.
        self.reason = reason

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    def send(self, frame: np.ndarray) -> None:
        self.frame_count += 1

    def close(self) -> None:
        self.closed = True


#: Why there is no virtual-camera backend on this OS yet. Linux publishes to a
#: v4l2loopback node and macOS into OBS's camera extension
#: (:mod:`benchhud.output.cmio`); this is what any other platform — Windows,
#: today — is told until it gets a backend of its own.
UNSUPPORTED_PLATFORM = "virtual-camera output is not available on this platform yet"


def open_output_sink(device: str, width: int, height: int) -> OutputSink:
    """Open the loopback output, falling back to a discarding sink.

    A missing ``v4l2loopback`` module, a wrong device path, or another writer
    already holding the node must leave the HUD usable — the bench still works
    without the OBS feed — so we log and fall back rather than fail.

    The fallback carries a ``reason`` so the shell can say output is inactive.
    Silently discarding frames while the operator believes they are streaming is
    the worst outcome, so the reason is surfaced rather than only logged.
    """
    if sys.platform.startswith("darwin"):
        from benchhud.output.cmio import CmioSinkUnavailable, open_cmio_sink

        try:
            return open_cmio_sink(width, height)
        except CmioSinkUnavailable as exc:
            # The reason rides on the exception, so it describes the failure
            # that actually happened — not a second probe that might disagree.
            log.warning("%s; frames will be discarded", exc.reason)
            return NullSink(width, height, reason=exc.reason)
    if not sys.platform.startswith("linux"):
        log.warning("%s; frames will be discarded", UNSUPPORTED_PLATFORM)
        return NullSink(width, height, reason=UNSUPPORTED_PLATFORM)
    try:
        return V4l2LoopbackSink(device, width, height)
    except (OSError, RuntimeError) as exc:
        log.warning("virtual-camera output unavailable on %s: %s", device, exc)
        return NullSink(width, height, reason=f"{device} unavailable ({exc})")
