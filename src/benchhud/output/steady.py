"""Keep a loopback output fed while the GUI thread is busy (#566).

The HUD composes on the GUI thread's frame timer and used to write each frame
there too, so a busy GUI thread meant no writes at all. OBS's v4l2 input waits
five frame periods for the next frame (166 ms at the 30 fps a v4l2loopback node
reports) and logs ``select timed out`` when none comes. On 2026-11-27 it logged
118 of them against the main output, most during a package update, and after the
burst its input stayed blank, though frames were flowing again, until its device
was re-opened.

:class:`SteadySink` moves the write onto a thread of its own:

* each new frame is copied and written as it comes;
* when none has come for :data:`REPEAT_AFTER_S`, the last one is written again,
  and again every :data:`REPEAT_AFTER_S` until a new one arrives.

So a GUI thread that's busy but lets go of the GIL (waiting on I/O, a lock, a
paint or a slow box) costs OBS a repeated frame, not a timeout. One that holds
the GIL the whole time (a long garbage collection, a C call that doesn't release
it) still stops this thread too. Python can't get round that.

Two things get a log line, so the next stall can be matched to OBS's timeouts and
to whatever else the bench was doing:

* a stretch of :data:`WRITE_GAP_LOG_S` or more between writes: long enough for
  OBS to time out, so this thread didn't get to run (or the write hung). At most
  one line every :data:`WRITE_GAP_LOG_EVERY_S`, counting the ones in between;
* a stretch of :data:`GAP_LOG_S` or more without a new frame from the GUI
  thread: once as it passes that, so a feed that froze for good still gets a
  line, and again when new frames come back, with how long they were gone.
  Repeats covered it, so OBS saw nothing wrong; the picture just stood still.

The copy is what keeps this stream-safe. The frame handed over can be an array
the GUI thread keeps using (the reader's latest frame, re-offered every tick),
and a repeat goes out later. Writing a copy means what reaches the loopback is
exactly what was on the frame when it was sent, whatever happens to the array
afterwards.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from benchhud.output.sink import OutputSink

log = logging.getLogger(__name__)

#: Write the last frame again after this long without a new one. Under OBS's
#: 166 ms select timeout, with room for a slow write.
REPEAT_AFTER_S = 0.1

#: A stretch without a new frame from the GUI thread this long gets a log line.
GAP_LOG_S = 1.0

#: A stretch between writes this long gets a log line: just under OBS's timeout.
WRITE_GAP_LOG_S = 0.15

#: At most one "between writes" line this often; the ones in between are counted.
WRITE_GAP_LOG_EVERY_S = 10.0

#: How long :meth:`SteadySink.close` waits for a write in progress to finish.
CLOSE_WAIT_S = 2.0


class SteadySink:
    """An :class:`~benchhud.output.sink.OutputSink` that writes on its own thread.

    A failure on the writer thread stops it, and the next :meth:`send` raises
    that error, so the caller drops the sink the way it would have when the
    write was synchronous.
    """

    def __init__(
        self,
        sink: OutputSink,
        *,
        name: str = "",
        repeat_after: float = REPEAT_AFTER_S,
        gap_log: float = GAP_LOG_S,
        write_gap_log: float = WRITE_GAP_LOG_S,
        write_gap_log_every: float = WRITE_GAP_LOG_EVERY_S,
        close_wait: float = CLOSE_WAIT_S,
    ) -> None:
        self._sink = sink
        self._name = name or str(getattr(sink, "device", "output"))
        self._repeat_after = repeat_after
        self._gap_log = gap_log
        self._write_gap_log = write_gap_log
        self._write_gap_log_every = write_gap_log_every
        self._close_wait = close_wait
        self._cond = threading.Condition()
        self._pending: np.ndarray | None = None
        self._closed = False
        self._error: BaseException | None = None
        # Who closes the inner sink: close() does, unless a write was still
        # running when it gave up waiting. Then the writer does, once the write
        # returns, so the descriptor is never closed under it (and reused by
        # the next open while the old write is still going).
        self._writer_done = False
        self._writer_closes = False
        #: Writes of the last frame again because no new one came.
        self.repeats = 0
        self._thread = threading.Thread(
            target=self._run, name=f"loopback writer {self._name}", daemon=True
        )
        self._thread.start()

    @property
    def size(self) -> tuple[int, int]:
        return self._sink.size

    @property
    def inner(self) -> OutputSink:
        """The sink frames are written to."""
        return self._sink

    def send(self, frame: np.ndarray) -> None:
        """Hand over one frame; it's copied, and written on the writer thread."""
        self._check_open()
        width, height = self._sink.size
        if frame.shape != (height, width, 3):
            raise ValueError(f"frame is {frame.shape}, sink expects {(height, width, 3)}")
        copy = np.array(frame, dtype=np.uint8, order="C", copy=True)
        with self._cond:
            self._check_open()
            self._pending = copy
            self._cond.notify()

    def _check_open(self) -> None:
        with self._cond:
            if self._error is not None:
                raise self._error
            if self._closed:
                raise RuntimeError("sink is closed")

    def close(self) -> None:
        """Stop the writer thread, then close the sink (idempotent)."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify()
        self._thread.join(self._close_wait)
        with self._cond:
            close_here = self._writer_done
            self._writer_closes = not close_here
        if close_here:
            self._sink.close()
        else:
            log.warning(
                "%s: the writer hadn't stopped after %.1f s; it closes the device when it does",
                self._name,
                self._close_wait,
            )

    def _next(self, last_write: float | None) -> np.ndarray | None:
        """Wait for a new frame, or until the last one written is due again.

        Returns the new frame, or None when it's time to repeat. With nothing
        written yet there's nothing to repeat, so it waits for a frame. Raises
        :class:`_Closed` once :meth:`close` has been called.
        """
        with self._cond:
            while self._pending is None and not self._closed:
                if last_write is None:
                    self._cond.wait()
                    continue
                left = last_write + self._repeat_after - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            if self._closed:
                raise _Closed
            new, self._pending = self._pending, None
            return new

    def _run(self) -> None:
        try:
            self._write_loop()
        finally:
            with self._cond:
                self._writer_done = True
                close_here = self._writer_closes
            if close_here:
                self._sink.close()

    def _write_loop(self) -> None:
        frame: np.ndarray | None = None
        last_write: float | None = None
        gaps = _WriteGaps(self._name, self._write_gap_log_every)
        fresh = _NewFrames(self._name, self._gap_log)
        try:
            while True:
                try:
                    new = self._next(last_write)
                except _Closed:
                    return
                now = time.monotonic()
                if last_write is not None and now - last_write >= self._write_gap_log:
                    gaps.gap(now, now - last_write)
                else:
                    gaps.flush(now)
                if new is None:
                    self.repeats += 1
                    fresh.repeat(now)
                else:
                    frame = new
                    fresh.new(now)
                assert frame is not None  # a write has happened, or one just came
                try:
                    self._sink.send(frame)
                except BaseException as exc:
                    log.warning("%s: write failed, the writer stopped: %s", self._name, exc)
                    with self._cond:
                        self._error = exc
                    return
                # When this write started, so a write that hangs shows in the next
                # gap, and repeats keep to the clock rather than drift by each write.
                last_write = now
        finally:
            gaps.flush(time.monotonic(), force=True)


class _NewFrames:
    """The "no new frame" lines: once a stretch passes ``gap_log``, and when it ends."""

    def __init__(self, name: str, gap_log: float) -> None:
        self._name = name
        self._gap_log = gap_log
        self._last: float | None = None
        self._repeats = 0
        self._said = False

    def new(self, now: float) -> None:
        if self._last is not None and now - self._last >= self._gap_log:
            log.warning(
                "%s: new frames again after %.1f s; wrote the last one %d more times meanwhile",
                self._name,
                now - self._last,
                self._repeats,
            )
        self._last, self._repeats, self._said = now, 0, False

    def repeat(self, now: float) -> None:
        self._repeats += 1
        if self._said or self._last is None or now - self._last < self._gap_log:
            return
        # Said as it starts, not only when it ends: a camera that never comes
        # back would otherwise freeze the feed without a line.
        self._said = True
        log.warning(
            "%s: no new frame for %.1f s; writing the last one again until one comes",
            self._name,
            now - self._last,
        )


class _WriteGaps:
    """The "between writes" lines: one at most every ``every`` seconds.

    Gaps in between are held back and counted. They're said with the next line,
    or on their own once ``every`` has passed with no new gap, or when the
    writer stops, so a burst that just ends isn't lost.
    """

    def __init__(self, name: str, every: float) -> None:
        self._name = name
        self._every = every
        self._logged_at: float | None = None
        self._held = 0
        self._worst = 0.0

    def gap(self, now: float, seconds: float) -> None:
        if self._logged_at is not None and now - self._logged_at < self._every:
            self._held += 1
            self._worst = max(self._worst, seconds)
            return
        more = (
            f" ({_gaps(self._held)} since the last line held back, the longest {self._worst:.2f} s)"
            if self._held
            else ""
        )
        log.warning("%s: %.2f s between writes%s", self._name, seconds, more)
        self._logged_at, self._held, self._worst = now, 0, 0.0

    def flush(self, now: float, *, force: bool = False) -> None:
        if not self._held or self._logged_at is None:
            return
        if force or now - self._logged_at >= self._every:
            log.warning(
                "%s: %s between writes since the last line, the longest %.2f s",
                self._name,
                _gaps(self._held),
                self._worst,
            )
            self._logged_at, self._held, self._worst = now, 0, 0.0


def _gaps(count: int) -> str:
    return f"{count} more gap" if count == 1 else f"{count} more gaps"


class _Closed(Exception):
    """The sink was closed while the writer waited."""
