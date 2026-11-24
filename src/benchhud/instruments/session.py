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

**Only the owner thread closes a port it's still using** (#532). :meth:`stop`
waits a couple of seconds; if a slow command is still running then, the thread
keeps the port, finishes the command, drops the output and closes the port on
its own way out. Closing it from underneath would turn that ``OUTP OFF`` into a
write to a closed port. A session started with ``after=`` the one it replaces
waits for that hand-back before it touches the wire, so one wire still has one
owner.

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

#: How long :meth:`InstrumentSession.stop` waits for the owner thread before
#: leaving it to finish, drop the output and close the port by itself (#532).
STOP_JOIN_TIMEOUT_S = 2.0

#: Longest a new session waits for the one it replaces to let go of the port.
#: Every exchange is bounded by the serial timeout, so a few seconds is the
#: worst a working port takes; past this the old thread is stuck in the driver
#: and waiting longer only leaves the meter blank. Going ahead does mean two
#: owners if the old thread ever comes back: its shutdown ``OUTP OFF`` would
#: then land in the middle of the new session.
PREVIOUS_SESSION_WAIT_S = 10.0


class InstrumentSessionStopped(Exception):
    """Submitted to a session that isn't running (or was stopped while queued)."""


class InstrumentSession:
    """Owns an :class:`OwonSpm8104` on one thread, polling and running commands."""

    def __init__(self, driver: OwonSpm8104, *, poll_interval: float = 0.2) -> None:
        self._driver = driver
        self._poll_interval = poll_interval
        #: (action, its future, whether it still runs once the session is stopping)
        self._queue: queue.SimpleQueue[tuple[InstrumentAction, Future, bool]] = queue.SimpleQueue()
        self._latest: TelemetrySnapshot | None = None
        self._failed_polls = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._after: InstrumentSession | None = None
        # Ownership of the port between stop() and the owner thread (#532),
        # both under _lock: whether the thread has let go of it, and whether
        # it should close it when it does.
        self._released = True
        self._close_on_exit = False
        #: Set once no thread of this session is using the port.
        self._finished = threading.Event()
        self._finished.set()

    # --- lifecycle -------------------------------------------------------

    def start(self, *, after: InstrumentSession | None = None) -> None:
        """Start the owner thread (idempotent).

        ``after`` is the session this one replaces on the same wire. The new
        thread writes nothing until that one has let go of its port (#532).
        """
        if self.is_running:
            return
        with self._lock:
            if not self._released:
                raise RuntimeError("this session's last run is still finishing a command")
            self._released = False
            self._close_on_exit = False
            self._failed_polls = 0
        self._after = after if after is not self else None
        self._finished.clear()
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="InstrumentSession", daemon=True)
        self._thread.start()

    def stop(self, *, close_driver: bool = True, timeout: float | None = None) -> None:
        """Stop the thread (idempotent) and, by default, close the driver.

        Waits ``timeout`` (default :data:`STOP_JOIN_TIMEOUT_S`) for the thread.
        If a command is still running after that, the thread keeps the port: it
        sends any queued Output OFF, drops the output and closes the port itself
        when the command returns (#532). :meth:`wait_finished` waits for that.
        Either way the session takes no new work once this returns.
        """
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(STOP_JOIN_TIMEOUT_S if timeout is None else timeout)
            self._thread = None
        with self._lock:
            handed_off = not self._released
            if handed_off:
                # A later stop(close_driver=False) doesn't take back an earlier close.
                self._close_on_exit = self._close_on_exit or close_driver
        if handed_off:
            _log.warning(
                "the instrument session is still busy; it drops the output and closes "
                "the port itself once it's done"
            )
            return
        self._fail_pending()
        if close_driver:
            self._driver.close()

    def wait_finished(self, timeout: float | None = None) -> bool:
        """Wait until no thread of this session is using the port; True if so."""
        return self._finished.wait(timeout)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- consumer API ----------------------------------------------------

    def latest(self) -> TelemetrySnapshot | None:
        """The most recent poll, or ``None`` before the first one lands."""
        with self._lock:
            return self._latest

    @property
    def failed_polls(self) -> int:
        """How many polls in a row have failed; 0 once one lands (#527).

        :func:`read_snapshot` never raises, so an instrument that's gone doesn't
        stop the thread. This is how a consumer tells "running" from "answering".
        """
        with self._lock:
            return self._failed_polls

    def submit(self, action: InstrumentAction[T], *, even_when_stopping: bool = False) -> Future[T]:
        """Queue a control action; it runs before the next poll.

        A command still queued when the session is told to stop is failed, not
        run (#530). ``even_when_stopping`` is the exception, for a command that
        can only make things safer: an Output OFF somebody pressed is still sent
        on the way out. A read-only session doesn't drop the rail itself, so
        that press may be the only thing that does.

        Every failure arrives the same way — on the returned future — so callers
        have one error path whether the command was refused by the driver, by
        the instrument, or because the session was stopped underneath them.
        """
        future: Future[T] = Future()
        if not self.is_running:
            future.set_exception(InstrumentSessionStopped("instrument session is not running"))
            return future
        self._queue.put((action, future, even_when_stopping))
        self._wake.set()
        return future

    # --- owner thread ----------------------------------------------------

    def _run(self) -> None:
        try:
            self._wait_for_previous()
            if self._stop.is_set():
                return  # stopped before the port was ours: the finally still drops it
            self._drop_output("on connect")
            while not self._stop.is_set():
                self._drain_commands()
                if self._stop.is_set():
                    break
                snapshot = read_snapshot(self._driver)
                with self._lock:
                    self._latest = snapshot
                    self._failed_polls = 0 if snapshot.ok else self._failed_polls + 1
                self._wake.wait(self._poll_interval)
                self._wake.clear()
        finally:
            # Whatever took the loop down — stop(), or an unexpected raise —
            # nothing new is started from here, bar a queued Output OFF.
            self._stop.set()
            # Each step runs even if the one before it raised something that
            # isn't an Exception, which none of them catch (#540).
            try:
                try:
                    self._drain_commands()
                finally:
                    # The rail must not outlive the thread that was watching it.
                    self._drop_output("on shutdown")
            finally:
                try:
                    # Never leave a caller blocked on a future this thread owned.
                    self._fail_pending()
                finally:
                    self._let_go()

    def _wait_for_previous(self) -> None:
        """Wait for the session this one replaced to let go of the wire (#532).

        Even when this one is stopped meanwhile: its shutdown still writes, and
        whoever waits on *this* session counts on the one before being done
        too. Past :data:`PREVIOUS_SESSION_WAIT_S` it goes ahead, with a log line.
        """
        previous, self._after = self._after, None  # don't keep a chain of old sessions
        if previous is not None and not previous.wait_finished(PREVIOUS_SESSION_WAIT_S):
            _log.warning("the last instrument session never let go of the port; going ahead")

    def _let_go(self) -> None:
        """Hand the port back; close it here if :meth:`stop` left that to us."""
        with self._lock:
            self._released = True
            close = self._close_on_exit
        try:
            if close:
                try:
                    self._driver.close()
                except Exception as exc:
                    _log.warning("could not close the instrument port: %s", exc)
        finally:
            # Whoever waits on this session waits for this, close or no close.
            self._finished.set()

    def _drain_commands(self) -> None:
        """Run every queued command. One attempt each — never a retry.

        A write that changes physical state is not idempotent: a retried
        ``OUTP ON`` after a timeout is the electrical version of double-counting
        stock. The failure goes to the caller's future and stops there.

        Once the session is stopping, only what was submitted
        ``even_when_stopping`` is started (#530): an ``OUTP ON`` waiting behind
        a slow command would bring the rail up just as the shutdown is about to
        drop it. The rest fails with :class:`InstrumentSessionStopped`.
        """
        while True:
            try:
                action, future, even_when_stopping = self._queue.get_nowait()
            except queue.Empty:
                return
            if not future.set_running_or_notify_cancel():
                continue
            if self._stop.is_set() and not even_when_stopping:
                future.set_exception(
                    InstrumentSessionStopped("instrument session stopped before the command ran")
                )
                continue
            try:
                future.set_result(action(self._driver))
            except Exception as exc:
                # A refused command must not take the session down with it.
                _log.warning("instrument command failed: %s", exc)
                future.set_exception(exc)
            except BaseException as exc:
                # This one does take the thread down, but its caller still hears (#540).
                future.set_exception(exc)
                raise

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
                _, future, _ = self._queue.get_nowait()
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
