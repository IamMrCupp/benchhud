"""Keep a loopback output written on a steady clock, whatever the GUI thread does (#566).

The composite is built on the GUI thread, once per frame tick. If the frame was
also written there, anything that held the GUI thread up (a slow paint, the
telemetry chart, a busy machine) held the loopback write up with it. OBS gives
up on a v4l2 node after five frame periods with nothing new: 166 ms on the
bench, where the node says 30 fps. It logs ``select timed out`` each time, and
on 2026-11-27 its input stayed blank after a burst of them.

:class:`PacedSink` takes the writes off the GUI thread. ``send`` only swaps in
the newest frame; a writer thread of its own writes each new frame as it
arrives, and writes the last one again whenever no new one has come for
:data:`REPEAT_AFTER_S`. So the node never goes quiet long enough for a reader to
time out, however late the next composite is.

It logs a warning when the output itself went more than ``gap_warning_s``
without a write, with how long. When new frames stop coming for that long it
logs once as the repeating starts, and again when they come back, with how long
they were gone. Each can be matched to OBS's log by time.

A frame that stops coming is repeated until a new one comes, without a limit.
A frozen picture on stream is visible and recoverable; a node that goes quiet
makes OBS time out, and on 2026-11-27 its input stayed blank afterwards.

Clock and wait are injected so tests run on simulated time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from benchhud.output.sink import OutputSink

log = logging.getLogger(__name__)

#: The longest the node goes without a write while the composite is late: under
#: OBS's 166 ms (five periods at the node's 30 fps), with room for the writer
#: itself being a little late.
REPEAT_AFTER_S = 0.1

#: Gaps longer than this get a log line.
GAP_WARNING_S = 1.0


class PacedSink:
    """An :class:`~benchhud.output.sink.OutputSink` that writes on its own thread.

    ``inner`` is the real sink; only the writer thread touches it. A write that
    fails stops the writer, and the next :meth:`send` raises the error, so the
    caller drops the sink the same way it would a synchronous one.
    """

    def __init__(
        self,
        inner: OutputSink,
        *,
        name: str = "",
        repeat_after: float = REPEAT_AFTER_S,
        gap_warning_s: float = GAP_WARNING_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._name = name or str(getattr(inner, "device", "output"))
        self._repeat_after = repeat_after
        self._gap_warning_s = gap_warning_s
        self._clock = clock
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._fresh = False  # a frame has come that isn't written yet
        self._closed = False
        self._exited = False  # the writer thread has returned
        self._close_on_exit = False  # close() gave up waiting: the writer closes
        self._stale_logged = False
        self._error: BaseException | None = None
        self._last_write: float | None = None
        self._last_fresh: float | None = None
        #: Frames written, new and repeated. For tests and diagnostics.
        self.writes = 0
        #: Of those, the repeats of a frame already written.
        self.repeats = 0
        self._thread = threading.Thread(target=self._run, name=f"paced:{self._name}", daemon=True)
        self._thread.start()

    @property
    def size(self) -> tuple[int, int]:
        return self._inner.size

    @property
    def inner(self) -> OutputSink:
        return self._inner

    def send(self, frame: np.ndarray) -> None:
        """Hand over the newest frame. Never waits on the device."""
        if self._error is not None:
            raise RuntimeError(f"{self._name}: write failed") from self._error
        if self._closed:
            raise RuntimeError("sink is closed")
        # Copied: the caller may draw on its frame after handing it over, and
        # the writer may still be writing the old one.
        copy = np.array(frame, dtype=np.uint8, copy=True)
        with self._cond:
            self._frame = copy
            self._fresh = True
            self._cond.notify()

    def close(self, timeout: float = 2.0) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout)
        with self._cond:
            if not self._exited:
                # A write stuck in the device. Closing the fd under it would let
                # the rest of that frame land on whatever opens next and takes
                # the same fd number, so the writer closes it when it returns.
                self._close_on_exit = True
                log.warning(
                    "%s: a write is still stuck after %.0f s; it closes when that returns",
                    self._name,
                    timeout,
                )
                return
        self._inner.close()

    # --- the writer thread --------------------------------------------------

    def _run(self) -> None:
        try:
            while True:
                with self._cond:
                    frame, fresh = self._next_frame()
                    if frame is None:
                        return  # closed
                self._write(frame, fresh)
        except Exception as exc:
            log.exception("%s: write failed; the output stops", self._name)
            self._error = exc
        finally:
            with self._cond:
                self._exited = True
                close = self._close_on_exit
            if close:
                self._inner.close()

    def _next_frame(self) -> tuple[np.ndarray | None, bool]:
        """Wait (holding the condition) for a new frame or the repeat deadline."""
        while not self._closed:
            if self._fresh:
                self._fresh = False
                return self._frame, True
            if self._frame is None:
                self._cond.wait()
                continue
            now = self._clock()
            last = now if self._last_write is None else self._last_write
            wait = last + self._repeat_after - now
            if wait <= 0:
                return self._frame, False
            self._cond.wait(wait)
        return None, False

    def _write(self, frame: np.ndarray, fresh: bool) -> None:
        now = self._clock()
        stale_for = 0.0 if self._last_fresh is None else now - self._last_fresh
        if fresh:
            if stale_for > self._gap_warning_s:
                log.warning(
                    "%s: new frames again after %.1f s; the last one was repeated meanwhile",
                    self._name,
                    stale_for,
                )
            self._last_fresh = now
            self._stale_logged = False
        else:
            self.repeats += 1
            if stale_for > self._gap_warning_s and not self._stale_logged:
                # Said once as it starts, not only when it ends: a camera that
                # never comes back would otherwise freeze the feed unlogged.
                self._stale_logged = True
                log.warning(
                    "%s: no new frame for %.1f s; repeating the last one until one comes",
                    self._name,
                    stale_for,
                )
        self._inner.send(frame)
        done = self._clock()
        if self._last_write is not None and done - self._last_write > self._gap_warning_s:
            log.warning("%s: %.1f s without a write", self._name, done - self._last_write)
        self._last_write = done
        self.writes += 1
