"""Frame pump — the glue between a capture reader and its consumers.

:class:`FramePump` is deliberately Qt-free: each :meth:`tick` pulls the freshest
frame from a :class:`~benchhud.capture.latest_frame_reader.LatestFrameReader` and
hands it to every registered consumer (a scope panel's ``update_frame``, an
output sink's ``send``, the compositor later). The app drives ``tick`` from a
QTimer on the GUI thread; tests drive it directly, so the dispatch logic needs
no event loop.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from benchhud.capture.latest_frame_reader import LatestFrameReader

FrameConsumer = Callable[[np.ndarray], None]


class FramePump:
    """Dispatches the reader's latest frame to consumers on each tick."""

    def __init__(
        self,
        reader: LatestFrameReader,
        consumers: list[FrameConsumer] | None = None,
    ) -> None:
        self._reader = reader
        self._consumers: list[FrameConsumer] = list(consumers or [])
        self.frames_dispatched = 0

    def add_consumer(self, consumer: FrameConsumer) -> None:
        self._consumers.append(consumer)

    def start(self) -> None:
        self._reader.start()

    def tick(self) -> bool:
        """Dispatch the latest frame. Returns False if none is available yet."""
        frame = self._reader.latest()
        if frame is None:
            return False
        for consumer in self._consumers:
            consumer(frame)
        self.frames_dispatched += 1
        return True

    def stop(self, *, close_source: bool = True) -> None:
        self._reader.stop(close_source=close_source)
