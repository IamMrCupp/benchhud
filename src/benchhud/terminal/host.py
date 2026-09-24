"""The terminal's state, shared between the shell, the GUI and the web pages.

One :class:`TerminalHost` owns the shell session and everything a page needs to
draw it: a bounded scrollback addressed by absolute character positions, the
terminal size, whether the shell has exited, and the on-stream share state. It
is Qt-free and thread-safe: the pty reader thread appends, the GUI thread
starts/restarts and toggles sharing, and each server thread waits in
:meth:`wait` for something new.

**Two tokens, two capabilities.** :attr:`write_token` is made fresh each run and
only ever handed to the in-app view: it's the one that may type into the shell.
The share token (persisted, so an OBS source URL survives a relaunch) can only
watch, and only gets output while sharing is on and not paused.

**The stream never sees what happened while it was paused.** Pausing moves
:attr:`share_floor` to the end of the output on resume; a share view is never
served anything before the floor, including when OBS reloads the source and
asks for the scrollback again.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from benchhud.terminal.pty_session import PtySession

#: Characters of scrollback kept. A page that connects late gets this much.
SCROLLBACK_CHARS = 200_000

SessionFactory = Callable[..., "PtySession"]


@dataclass(frozen=True)
class TerminalState:
    """One consistent read of everything but the text."""

    version: int
    session: int  # bumps on every (re)start, so pages know to clear
    cols: int
    rows: int
    exit_code: int | None  # None while the shell runs (or before it starts)
    running: bool
    error: str | None  # why the shell couldn't start
    sharing: bool
    paused: bool
    share_floor: int
    end: int


class TerminalHost:
    """The shell session plus the scrollback and share state the pages read."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        share_token: str,
        session_factory: SessionFactory | None = None,
        scrollback: int = SCROLLBACK_CHARS,
    ) -> None:
        self._argv = list(argv)
        if session_factory is None:
            from benchhud.terminal.pty_session import PtySession  # POSIX only

            session_factory = PtySession
        self._session_factory = session_factory
        self._scrollback = max(1024, scrollback)
        self.write_token = secrets.token_urlsafe(24)
        self.share_token = share_token
        self._cond = threading.Condition()
        self._text = ""
        self._start = 0  # absolute position of self._text[0]
        self._version = 0
        self._wakes = 0
        self._session_no = 0
        self._cols, self._rows = 100, 30
        self._exit_code: int | None = None
        self._error: str | None = None
        self._sharing = False
        self._paused = False
        self._share_floor = 0
        self._session: PtySession | None = None

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    @property
    def running(self) -> bool:
        return self._session is not None and self._session.running

    # --- lifecycle (GUI thread) -------------------------------------------

    def start(self) -> None:
        """Start the shell if it isn't running (idempotent)."""
        if self._session is not None and self._session.running:
            return
        with self._cond:
            self._session_no += 1
            session_no = self._session_no
            self._text, self._start = "", self._end_locked()
            self._share_floor = max(self._share_floor, self._start)
            self._exit_code = None
            self._error = None
            self._bump_locked()
        session = self._session_factory(
            self._argv,
            on_output=lambda text: self._append(session_no, text),
            on_exit=lambda code: self._exited(session_no, code),
            cols=self._cols,
            rows=self._rows,
        )
        try:
            session.start()
        except OSError as exc:
            with self._cond:
                self._error = f"couldn't start {self._argv[0]}: {exc.strerror or exc}"
                self._bump_locked()
            return
        self._session = session

    def stop(self) -> None:
        """Hang up on the shell (idempotent)."""
        session, self._session = self._session, None
        if session is not None:
            session.close()

    def restart(self) -> None:
        self.stop()
        self.start()

    def close(self) -> None:
        """Stop the shell and release every waiting page, for shutdown."""
        self.stop()
        self.wake_all()

    # --- from the shell (reader thread) ------------------------------------

    def _append(self, session_no: int, text: str) -> None:
        with self._cond:
            if session_no != self._session_no:
                return  # a late read from a session we've already replaced
            self._text += text
            overflow = len(self._text) - self._scrollback
            if overflow > 0:
                self._text = self._text[overflow:]
                self._start += overflow
            self._bump_locked()

    def _exited(self, session_no: int, code: int) -> None:
        with self._cond:
            if session_no != self._session_no:
                return
            self._exit_code = code
            self._bump_locked()

    # --- from the pages (server threads) -----------------------------------

    def write(self, text: str) -> None:
        session = self._session
        if session is not None:
            session.write(text)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(2, min(int(cols), 1000)), max(1, min(int(rows), 1000))
        with self._cond:
            if (cols, rows) == (self._cols, self._rows):
                return
            self._cols, self._rows = cols, rows
            self._bump_locked()
        session = self._session
        if session is not None:
            session.resize(cols, rows)

    def state(self) -> TerminalState:
        with self._cond:
            return self._state_locked()

    def read(self, pos: int, floor: int = 0) -> tuple[int, str, bool]:
        """Text from ``pos`` (never before ``floor``) to the end.

        Returns ``(new_pos, text, skipped)``; ``skipped`` means some of what was
        asked for is gone (scrolled out, or below the floor), so the page should
        clear before drawing.
        """
        with self._cond:
            begin = max(pos, floor, self._start)
            skipped = begin > pos
            text = self._text[begin - self._start :]
            return begin + len(text), text, skipped

    def wait(self, pos: int, version: int, timeout: float) -> None:
        """Block until there's text past ``pos``, the state moves past ``version``,
        :meth:`wake_all` is called, or ``timeout`` passes."""
        with self._cond:
            wakes = self._wakes
            self._cond.wait_for(
                lambda: (
                    self._wakes != wakes or self._version != version or self._end_locked() > pos
                ),
                timeout=timeout,
            )

    def wake_all(self) -> None:
        with self._cond:
            self._wakes += 1
            self._cond.notify_all()

    # --- share state (GUI thread) --------------------------------------------

    def set_sharing(self, sharing: bool) -> None:
        """Start or stop the on-stream view. Turning it on starts from now."""
        with self._cond:
            if sharing == self._sharing:
                return
            self._sharing = sharing
            if sharing:
                self._share_floor = self._end_locked()
            self._bump_locked()

    def set_paused(self, paused: bool) -> None:
        """Pause the on-stream view. On resume it picks up from now, skipping the gap."""
        with self._cond:
            if paused == self._paused:
                return
            self._paused = paused
            if not paused:
                self._share_floor = self._end_locked()
            self._bump_locked()

    def token_mode(self, token: str) -> str | None:
        """``"write"`` for the in-app token, ``"share"`` for the stream's, else None.

        A share token always loads the page; whether it gets output is decided
        per event (sharing on, not paused), so an OBS source left in a scene
        comes alive when sharing is switched on.
        """
        if not token:
            return None
        if secrets.compare_digest(token, self.write_token):
            return "write"
        if self.share_token and secrets.compare_digest(token, self.share_token):
            return "share"
        return None

    # --- internals -------------------------------------------------------------

    def _end_locked(self) -> int:
        return self._start + len(self._text)

    def _bump_locked(self) -> None:
        self._version += 1
        self._cond.notify_all()

    def _state_locked(self) -> TerminalState:
        session = self._session
        return TerminalState(
            version=self._version,
            session=self._session_no,
            cols=self._cols,
            rows=self._rows,
            exit_code=self._exit_code,
            running=session is not None and session.running,
            error=self._error,
            sharing=self._sharing,
            paused=self._paused,
            share_floor=self._share_floor,
            end=self._end_locked(),
        )
