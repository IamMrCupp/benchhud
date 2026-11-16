"""A shell running in a Windows pseudo-console (ConPTY), read on its own thread.

The Windows counterpart of :class:`~benchhud.terminal.pty_session.PtySession`,
with the same shape (``start``, ``write``, ``resize``, ``close``, ``running``;
output and exit via callbacks), so :class:`~benchhud.terminal.host.TerminalHost`
and everything above it is unchanged (#203).

The pseudo-console comes from `pywinpty <https://github.com/andfoy/pywinpty>`_
(MIT), a Windows-only dependency. It is imported when a session starts, never
at module level, so this module imports on every OS and its logic is tested
everywhere against a stand-in for ``winpty.PTY``.

**Reading polls.** A ConPTY's output pipe stays open after the shell exits, so
a blocking read can wait forever on a shell that has gone. The reader asks for
whatever is there without blocking and checks whether the process is alive in
between: quickly while output is flowing, slowly when idle.

**Closing** ends the shell's process, then lets go of the pseudo-console.
Windows closes whatever is still attached to a console when it goes away, which
is its version of the hang-up a POSIX shell's children get.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from benchhud.terminal.env import shell_env

#: Seconds between reads: while output is flowing, and once it has gone quiet.
_BUSY_POLL = 0.002
_IDLE_POLL = 0.02
#: How long after the last output the reader still counts as busy.
_BUSY_WINDOW = 0.25
#: After the shell exits, how long the output must stay quiet before we stop.
_DRAIN_QUIET = 0.05

PtyOpener = Callable[[int, int], Any]


def open_conpty(cols: int, rows: int) -> Any:
    """A ``winpty.PTY`` on the ConPTY backend. Windows only."""
    import winpty  # pywinpty; Windows only

    return winpty.PTY(cols, rows, backend=winpty.Backend.ConPTY)


def env_block(env: Mapping[str, str]) -> str:
    """``CreateProcess``'s environment block: ``name=value`` pairs, NUL-separated."""
    return "".join(f"{key}={value}\0" for key, value in env.items())


def command_tail(argv: Sequence[str]) -> str | None:
    """Everything after the program, quoted the way Windows programs parse it.

    pywinpty appends this to the program name as it stands, so it starts with
    the separating space. None when there are no arguments.
    """
    if len(argv) < 2:
        return None
    return " " + subprocess.list2cmdline(list(argv[1:]))


class ConPtySession:
    """Run ``argv`` in a ConPTY pseudo-console; stream its output, accept input."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        on_output: Callable[[str], None],
        on_exit: Callable[[int], None],
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        cols: int = 100,
        rows: int = 30,
        open_pty: PtyOpener | None = None,
    ) -> None:
        if not argv:
            raise ValueError("argv is empty")
        self._argv = list(argv)
        self._on_output = on_output
        self._on_exit = on_exit
        self._cwd = cwd or os.path.expanduser("~")
        self._env = dict(env) if env is not None else shell_env()
        self._size = (cols, rows)
        self._open_pty = open_pty or open_conpty
        self._pty: Any = None
        self._pid: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Spawn the process. Raises ``FileNotFoundError`` for a missing program,
        and ``OSError`` if the pseudo-console can't be made."""
        program = shutil.which(self._argv[0], path=self._env.get("PATH"))
        if program is None:
            raise FileNotFoundError(f"{self._argv[0]}: not found")
        # pywinpty raises its own WinptyError (a bare Exception subclass) for
        # everything; the host reports OSError, so translate.
        try:
            pty = self._open_pty(*self._size)
        except ImportError as exc:
            raise OSError(f"pywinpty isn't installed ({exc})") from exc
        except Exception as exc:
            raise OSError(f"no pseudo-console: {exc}") from exc
        try:
            spawned = pty.spawn(
                program,
                cmdline=command_tail(self._argv),
                cwd=self._cwd,
                env=env_block(self._env),
            )
        except Exception as exc:
            raise OSError(str(exc)) from exc
        if not spawned:
            raise OSError("the process didn't start")
        self._pty, self._pid = pty, pty.pid
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._read_loop, args=(pty,), name="conpty-reader", daemon=True
        )
        self._thread.start()

    def _read_loop(self, pty: Any) -> None:
        last_output = 0.0
        while not self._stop.is_set():
            text = self._read(pty)
            if text is None:  # the pipe is gone
                break
            if text:
                self._on_output(text)
                last_output = time.monotonic()
                continue
            if not self._alive(pty):
                # It has exited: take what it wrote on the way out, then stop.
                # The console host can still be flushing, so allow it a moment.
                quiet_since = time.monotonic()
                while time.monotonic() - quiet_since < _DRAIN_QUIET:
                    tail = self._read(pty)
                    if tail is None:
                        break
                    if tail:
                        self._on_output(tail)
                        quiet_since = time.monotonic()
                    else:
                        time.sleep(_BUSY_POLL)
                break
            busy = time.monotonic() - last_output < _BUSY_WINDOW
            self._stop.wait(_BUSY_POLL if busy else _IDLE_POLL)
        self._on_exit(self._exit_code(pty))

    @staticmethod
    def _read(pty: Any) -> str | None:
        """What's waiting, ``""`` for nothing yet, None once reading fails."""
        try:
            return pty.read(blocking=False) or ""
        except Exception:  # WinptyError: end of file, or the console closed
            return None

    @staticmethod
    def _alive(pty: Any) -> bool:
        try:
            return bool(pty.isalive())
        except Exception:
            return False

    def _exit_code(self, pty: Any) -> int:
        """The process's exit code; 0 if Windows won't say (still running, or gone)."""
        deadline = time.monotonic() + 0.5
        while True:
            try:
                code = pty.get_exitstatus()
            except Exception:
                return 0
            if code is not None:
                return int(code)
            if self._stop.is_set() or time.monotonic() > deadline:
                return 0
            time.sleep(0.01)

    def write(self, text: str) -> None:
        """Send keystrokes to the shell. Silently dropped once it's gone."""
        pty = self._pty
        if pty is None or not self.running:
            return
        with self._write_lock, contextlib.suppress(Exception):
            pty.write(text)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(2, int(cols)), max(1, int(rows))
        self._size = (cols, rows)
        pty = self._pty
        if pty is not None and self.running:
            with contextlib.suppress(Exception):
                pty.set_size(cols, rows)

    def close(self, timeout: float = 1.0) -> None:
        """End the shell, then let go of the pseudo-console (idempotent)."""
        pid = self._pid
        if pid is not None and self.running:
            # TerminateProcess on Windows. The reader sees it exit and finishes.
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + timeout
            while self.running and time.monotonic() < deadline:
                time.sleep(0.02)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # Dropping the last reference closes the pseudo-console, and Windows
        # closes anything the shell left attached to it.
        self._pty = None
