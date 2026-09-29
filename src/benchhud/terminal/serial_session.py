"""Serial consoles for the terminal panel (#174): what it can attach to besides a shell.

Both classes have :class:`~benchhud.terminal.pty_session.PtySession`'s shape
(``start``, ``write``, ``resize``, ``close``, ``running``; output and exit via
callbacks), so :class:`~benchhud.terminal.host.TerminalHost`, the pages and the
on-stream view work unchanged.

* :class:`SerialSession`: a raw serial port, PuTTY-style. Bytes in, bytes out,
  with the line-ending and local-echo settings serial devices differ on.
* :class:`OwonConsoleSession`: the OWON, which is never opened a second time.
  Every line goes through the running :class:`InstrumentSession`, so it can't
  interleave with a poll, and through :meth:`OwonSpm8104.raw_query`, so only
  queries are ever sent. Setpoints stay with the driver, where the limits are.
"""

from __future__ import annotations

import codecs
import contextlib
import threading
from collections.abc import Callable

#: What Enter sends to a device, by setting name.
LINE_ENDINGS = {"cr": "\r", "lf": "\n", "crlf": "\r\n"}

Opener = Callable[..., object]


def _to_terminal(text: str) -> str:
    """Show a lone ``\\n`` as a new line: devices often send bare LF."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


class SerialSession:
    """A raw serial port as a terminal session."""

    def __init__(
        self,
        url: str,
        *,
        on_output: Callable[[str], None],
        on_exit: Callable[[int], None],
        baud: int = 115200,
        line_ending: str = "crlf",
        local_echo: bool = False,
        opener: Opener | None = None,
        **_ignored,
    ) -> None:
        self._url = url
        self._on_output = on_output
        self._on_exit = on_exit
        self._baud = baud
        self._enter = LINE_ENDINGS.get(line_ending, "\r\n")
        self._local_echo = local_echo
        self._opener = opener
        self._port = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Open the port. Raises ``OSError`` (pyserial's SerialException) if it can't."""
        opener = self._opener
        if opener is None:
            import serial

            opener = serial.serial_for_url
        try:
            self._port = opener(self._url, baudrate=self._baud, timeout=0.1)
        except Exception as exc:
            raise OSError(str(exc)) from exc
        self._on_output(f"\x1b[2m[connected to {self._url} at {self._baud} baud]\x1b[0m\r\n")
        self._thread = threading.Thread(target=self._read_loop, name="serial-reader", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        code = 0
        try:
            while not self._stop.is_set():
                data = self._port.read(max(1, getattr(self._port, "in_waiting", 0) or 1))
                if data:
                    text = decoder.decode(data)
                    if text:
                        self._on_output(_to_terminal(text))
        except Exception as exc:  # unplugged, or the driver went away
            if not self._stop.is_set():
                self._on_output(f"\r\n\x1b[31m[{self._url} disconnected: {exc}]\x1b[0m\r\n")
                code = 1
        self._on_exit(code)

    def write(self, text: str) -> None:
        """Keystrokes to the device: Enter becomes the configured line ending."""
        port = self._port
        if port is None or not self.running:
            return
        out = text.replace("\r", self._enter)
        if self._local_echo:
            self._on_output(_to_terminal(text.replace("\r", "\n")))
        # A failed write means a disconnect, which the reader reports.
        with self._write_lock, contextlib.suppress(Exception):
            port.write(out.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        """A serial line has no window size."""

    def close(self, timeout: float = 1.0) -> None:
        self._stop.set()
        port, self._port = self._port, None
        if port is not None:
            with contextlib.suppress(Exception):
                port.close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)


class OwonConsoleSession:
    """A line-at-a-time SCPI query console on the OWON, through its session."""

    PROMPT = "owon> "
    BANNER = (
        "\x1b[2mOWON SPM8104 SCPI console. Queries only (a line ending in ?), e.g. "
        "*IDN?  MEAS:VOLT?  CONFigure:ALL?\r\n"
        "Setpoints and output go through the Meter panel's controls, where the "
        "limits apply.\x1b[0m\r\n"
    )

    def __init__(
        self,
        instrument_session,
        *,
        on_output: Callable[[str], None],
        on_exit: Callable[[int], None],
        **_ignored,
    ) -> None:
        self._session = instrument_session
        self._on_output = on_output
        self._on_exit = on_exit
        self._line = ""
        self._running = False
        self._busy = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._session is None or not self._session.is_running:
            raise OSError("the OWON isn't connected (turn on telemetry in Preferences)")
        self._running = True
        self._on_output(self.BANNER + self.PROMPT)

    def write(self, text: str) -> None:
        """Local line editing; Enter sends the line as one query."""
        if not self._running:
            return
        for ch in text:
            if ch in "\r\n":
                self._submit()
            elif ch in "\x7f\b":
                with self._lock:
                    if self._line:
                        self._line = self._line[:-1]
                        self._on_output("\b \b")
            elif ch == "\x03":  # Ctrl-C drops the line
                with self._lock:
                    self._line = ""
                self._on_output("^C\r\n" + self.PROMPT)
            elif ch == "\x1b" or ord(ch) < 32:
                continue  # arrow keys and other controls: not a line editor
            else:
                with self._lock:
                    self._line += ch
                self._on_output(ch)

    def _submit(self) -> None:
        with self._lock:
            line, self._line = self._line.strip(), ""
        self._on_output("\r\n")
        if not line:
            self._on_output(self.PROMPT)
            return
        future = self._session.submit(lambda driver: driver.raw_query(line))
        future.add_done_callback(self._answer)

    def _answer(self, future) -> None:
        try:
            reply = future.result()
            text = f"{reply}\r\n"
        except Exception as exc:
            text = f"\x1b[31m{exc}\x1b[0m\r\n"
        self._on_output(text + self.PROMPT)

    def resize(self, cols: int, rows: int) -> None:
        """Line-at-a-time: nothing to size."""

    def close(self) -> None:
        if self._running:
            self._running = False
            self._on_exit(0)
