"""Serial transport seam for instrument drivers.

Instrument drivers depend on the :class:`SerialPort` protocol rather than on
``pyserial`` directly, so they can be unit-tested against a fake transport with
no hardware attached (see ``tests/conftest.py``). :class:`LineInstrument` layers
the SCPI-style "write a line, read a line" convention used by the OWON SPM8104
(and most bench instruments) on top of any :class:`SerialPort`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SerialPort(Protocol):
    """Minimal byte transport an instrument driver needs.

    ``pyserial``'s ``serial.Serial`` satisfies this structurally, as does the
    test fake. Keeping the surface tiny keeps the fake honest.
    """

    def write(self, data: bytes) -> int:
        """Write ``data``; return the number of bytes written."""
        ...

    def readline(self) -> bytes:
        """Read up to and including the next newline (or until timeout)."""
        ...

    def close(self) -> None:
        """Release the underlying port."""
        ...


class LineInstrument:
    """SCPI-style line protocol over a :class:`SerialPort`.

    Commands are newline-terminated; responses are read a line at a time and
    stripped. This matches the OWON SPM8104 (115200 baud, newline-terminated —
    see ``research.md`` §4).
    """

    def __init__(
        self,
        port: SerialPort,
        *,
        encoding: str = "ascii",
        terminator: str = "\n",
    ) -> None:
        self._port = port
        self._encoding = encoding
        self._terminator = terminator

    def command(self, command: str) -> None:
        """Send a command with no expected response."""
        self._port.write((command + self._terminator).encode(self._encoding))

    def query(self, command: str) -> str:
        """Send ``command`` and return the stripped response line."""
        self.command(command)
        return self._port.readline().decode(self._encoding, errors="replace").strip()

    def close(self) -> None:
        self._port.close()
