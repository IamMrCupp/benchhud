"""Threaded latest-frame reader.

Wraps a :class:`~benchhud.capture.frame_source.FrameSource` in a background
thread that keeps only the *most recent* frame. Consumers call :meth:`latest`
and never block on a slow feed — the compositor pacing (Phase 4) reads whichever
scope/thermal frame is freshest and moves on, so one slow camera can't stall the
loop (plan §4/§10).

A real camera's ``read()`` blocks until the next frame, which paces the thread
naturally. For a non-blocking source (e.g. :class:`SyntheticFrameSource` used as
a live dev fallback), pass ``poll_interval`` to throttle instead of spinning.
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType

import numpy as np

from benchhud.capture.frame_source import FrameSource

_log = logging.getLogger(__name__)


class LatestFrameReader:
    """Continuously read a :class:`FrameSource`, exposing only the latest frame."""

    def __init__(self, source: FrameSource, *, poll_interval: float = 0.0) -> None:
        self._source = source
        self._poll_interval = poll_interval
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        """Start the capture thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._error = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="LatestFrameReader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                frame = self._source.read()
                if frame is not None:
                    with self._lock:
                        self._latest = frame
                if self._poll_interval:
                    self._stop.wait(self._poll_interval)
        except Exception as exc:
            # Surface any source failure instead of dying silently in the thread.
            self._error = exc
            _log.exception("capture thread stopped on error")

    def latest(self) -> np.ndarray | None:
        """Return the most recent frame, or ``None`` if none captured yet."""
        with self._lock:
            return self._latest

    @property
    def error(self) -> BaseException | None:
        """The exception that stopped the capture thread, if any."""
        return self._error

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, *, close_source: bool = True, timeout: float = 2.0) -> None:
        """Stop the thread (idempotent) and, by default, close the source."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        if close_source:
            self._source.close()

    def __enter__(self) -> LatestFrameReader:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
