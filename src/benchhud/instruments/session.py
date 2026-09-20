"""Single-owner instrument session: one thread, one port, reads and writes.

The OWON is a half-duplex line protocol, and once the HUD both polls it and
writes to it there are two sources of traffic on one wire. A ``MEAS:VOLT?`` in
flight when someone hits Output On gets the write interleaved mid-exchange, and
the response is read back against the wrong request — voltage parsed from a
command echo, or ``float()`` raising on an acknowledgement. **The port has
exactly one owner thread; everything else submits requests.**

Shape follows :class:`~benchhud.capture.latest_frame_reader.LatestFrameReader`:
the thread owns the blocking resource, consumers call :meth:`latest` and never
block. That matters beyond tidiness — a wedged port sits in pyserial's timeout
for a full second, and the composite pipeline is real-time. The session thread
absorbs that; the HUD sees a stale snapshot, not a dropped frame.

**Commands jump ahead of polls.** A poll that waits 200 ms is invisible; an
output-enable that waits 200 ms feels broken. Submitting also wakes the loop
early rather than letting it sit out the poll interval.

**The rail is dropped on connect and on shutdown** when the session can control
the instrument at all. Inheriting whatever the front panel was left at is how a
board gets powered at the wrong voltage by a HUD nobody asked to energize
anything; ``OUTP OFF`` first, then read. A read-only session (control off) stays
genuinely read-only and writes nothing.

Qt-free by design, like everything else in ``instruments/`` — the shell drives
it, the same way :class:`FramePump` drives capture.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from types import TracebackType
from typing import TypeVar

from benchhud.instruments.owon_spm8104 import OwonSpm8104
from benchhud.instruments.telemetry import TelemetrySnapshot, read_snapshot

_log = logging.getLogger(__name__)

T = TypeVar("T")

#: A control action, run on the owner thread with exclusive use of the port.
InstrumentAction = Callable[[OwonSpm8104], T]


class InstrumentSessionStopped(Exception):
    """Submitted to a session that isn't running (or was stopped while queued)."""


class InstrumentSession:
    """Owns an :class:`OwonSpm8104` on one thread, polling and running commands."""

    def __init__(self, driver: OwonSpm8104, *, poll_interval: float = 0.2) -> None:
        self._driver = driver
        self._poll_interval = poll_interval
        self._queue: queue.SimpleQueue[tuple[InstrumentAction, Future]] = queue.SimpleQueue()
        self._latest: TelemetrySnapshot | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start the owner thread (idempotent)."""
        if self.is_running:
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="InstrumentSession", daemon=True)
        self._thread.start()

    def stop(self, *, close_driver: bool = True, timeout: float = 2.0) -> None:
        """Stop the thread (idempotent) and, by default, close the driver."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self._fail_pending()
        if close_driver:
            self._driver.close()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- consumer API ----------------------------------------------------

    def latest(self) -> TelemetrySnapshot | None:
        """The most recent poll, or ``None`` before the first one lands."""
        with self._lock:
            return self._latest

    def submit(self, action: InstrumentAction[T]) -> Future[T]:
        """Queue a control action; it runs before the next poll.

        Every failure arrives the same way — on the returned future — so callers
        have one error path whether the command was refused by the driver, by
        the instrument, or because the session was stopped underneath them.
        """
        future: Future[T] = Future()
        if not self.is_running:
            future.set_exception(InstrumentSessionStopped("instrument session is not running"))
            return future
        self._queue.put((action, future))
        self._wake.set()
        return future

    # --- owner thread ----------------------------------------------------

    def _run(self) -> None:
        try:
            self._drop_output("on connect")
            while not self._stop.is_set():
                self._drain_commands()
                if self._stop.is_set():
                    break
                snapshot = read_snapshot(self._driver)
                with self._lock:
                    self._latest = snapshot
                self._wake.wait(self._poll_interval)
                self._wake.clear()
        finally:
            # Whatever took the loop down — stop(), or an unexpected raise — the
            # rail must not outlive the thread that was watching it.
            self._drop_output("on shutdown")
            # Never leave a caller blocked on a future this thread owned.
            self._fail_pending()

    def _drain_commands(self) -> None:
        """Run every queued command. One attempt each — never a retry.

        A write that changes physical state is not idempotent: a retried
        ``OUTP ON`` after a timeout is the electrical version of double-counting
        stock. The failure goes to the caller's future and stops there.
        """
        while True:
            try:
                action, future = self._queue.get_nowait()
            except queue.Empty:
                return
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(action(self._driver))
            except Exception as exc:
                # A refused command must not take the session down with it.
                _log.warning("instrument command failed: %s", exc)
                future.set_exception(exc)

    def _drop_output(self, when: str) -> None:
        """Best-effort ``OUTP OFF``. Never raises — this runs on failure paths.

        Skipped entirely when control is off, so a read-only session doesn't
        write to an instrument the operator only asked it to watch.
        """
        if not self._driver.control_enabled:
            return
        try:
            self._driver.output_off()
        except Exception as exc:
            # A wedged port is exactly when this fails, and exactly when raising
            # would be useless — the operator needs the log line, not a crash.
            _log.warning("could not drop the output %s: %s", when, exc)

    def _fail_pending(self) -> None:
        """Resolve anything still queued, so no caller waits forever."""
        while True:
            try:
                _, future = self._queue.get_nowait()
            except queue.Empty:
                return
            if future.set_running_or_notify_cancel():
                future.set_exception(
                    InstrumentSessionStopped("instrument session stopped before the command ran")
                )

    # --- context manager -------------------------------------------------

    def __enter__(self) -> InstrumentSession:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
