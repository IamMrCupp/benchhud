"""A shell running in a POSIX pseudo-terminal, read on its own thread.

``pty.fork`` gives the child a session and a controlling terminal, which an
interactive shell needs for job control (Ctrl-C, Ctrl-Z, ``fg``). The child
does nothing but ``execve``: every argument is resolved in the parent first,
so no Python runs between the fork and the exec.

Output is decoded incrementally as UTF-8, so a character split across two reads
arrives whole, and handed to ``on_output`` from the reader thread. The caller
owns thread safety on that side (:class:`~benchhud.terminal.host.TerminalHost`
takes a lock).

macOS and Linux only. Windows needs ConPTY, which is a separate backend.
"""

from __future__ import annotations

import codecs
import contextlib
import fcntl
import os
import pty
import shutil
import signal
import struct
import termios
import threading
import time
import warnings
from collections.abc import Callable, Mapping, Sequence

#: Variables the packaged app sets for its own bundled Python. Leaking them into
#: the operator's shell would break their own ``python``.
_STRIP_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE")


def shell_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for the shell: ours, minus the app's Python, plus a TERM."""
    env = dict(base if base is not None else os.environ)
    for key in _STRIP_ENV:
        env.pop(key, None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    return env


def _max_fd() -> int:
    """One past the highest fd this process can hold (resolved before fork)."""
    try:
        limit = os.sysconf("SC_OPEN_MAX")
    except (OSError, ValueError):
        limit = -1
    return limit if limit > 0 else 65536


def set_winsize(fd: int, cols: int, rows: int) -> None:
    """Tell the terminal its size. The kernel sends the shell SIGWINCH."""
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class PtySession:
    """Run ``argv`` in a pseudo-terminal; stream its output, accept input."""

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
    ) -> None:
        if not argv:
            raise ValueError("argv is empty")
        self._argv = list(argv)
        self._on_output = on_output
        self._on_exit = on_exit
        self._cwd = cwd or os.path.expanduser("~")
        self._env = dict(env) if env is not None else shell_env()
        self._size = (cols, rows)
        self._pid: int | None = None
        self._fd: int | None = None
        self._thread: threading.Thread | None = None
        self._write_lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Spawn the process. Raises ``FileNotFoundError`` for a missing program."""
        program = shutil.which(self._argv[0], path=self._env.get("PATH"))
        if program is None:
            raise FileNotFoundError(f"{self._argv[0]}: not found")
        argv, env, cwd = self._argv, self._env, self._cwd
        max_fd = _max_fd()
        with warnings.catch_warnings():
            # Forking a threaded process warns because the child could deadlock
            # on a lock another thread held. This child only calls execve.
            warnings.simplefilter("ignore", DeprecationWarning)
            pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - the child; it never returns
            # Drop everything but the pty (0-2). C libraries open fds without
            # O_CLOEXEC — OpenCV's V4L2 cameras among them — and a shell job
            # that outlives the app would otherwise keep those cameras locked.
            os.closerange(3, max_fd)
            with contextlib.suppress(OSError):
                os.chdir(cwd)
            try:
                os.execve(program, argv, env)
            finally:
                os._exit(127)
        self._pid, self._fd = pid, fd
        set_winsize(fd, *self._size)
        self._thread = threading.Thread(target=self._read_loop, name="pty-reader", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = self._fd
        assert fd is not None
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:  # EIO on Linux once the child has gone
                data = b""
            if not data:
                break
            text = decoder.decode(data)
            if text:
                self._on_output(text)
        tail = decoder.decode(b"", final=True)
        if tail:
            self._on_output(tail)
        self._on_exit(self._reap())

    def _reap(self) -> int:
        """Wait for the child and return its exit code (128 + signal if killed)."""
        if self._pid is None:
            return 0
        try:
            _, status = os.waitpid(self._pid, 0)
        except ChildProcessError:
            return 0
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
        return os.WEXITSTATUS(status)

    def write(self, text: str) -> None:
        """Send keystrokes to the shell. Silently dropped once it's gone."""
        fd = self._fd
        if fd is None or not self.running:
            return
        data = text.encode("utf-8")
        with self._write_lock:
            while data:
                try:
                    written = os.write(fd, data)
                except OSError:
                    return
                data = data[written:]

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(2, int(cols)), max(1, int(rows))
        self._size = (cols, rows)
        if self._fd is not None and self.running:
            with contextlib.suppress(OSError):
                set_winsize(self._fd, cols, rows)

    def close(self, timeout: float = 1.0) -> None:
        """Hang up on the shell, then kill it if it lingers (idempotent)."""
        pid = self._pid
        if pid is not None and self.running:
            for sig in (signal.SIGHUP, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                deadline = time.monotonic() + timeout
                while self.running and time.monotonic() < deadline:
                    time.sleep(0.02)
                if not self.running:
                    break
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self._fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None
