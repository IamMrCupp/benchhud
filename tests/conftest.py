"""Shared test fixtures.

The star here is a hardware-free **fake SCPI serial port**: instrument drivers
depend on the ``SerialPort`` protocol, so they can be driven end to end in CI
against this fake. Phase 2's OWON driver reuses these fixtures.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

# Verified OWON SPM8104 responses (measured on the bench — research.md §4).
OWON_SPM8104_RESPONSES: dict[str, str] = {
    "*IDN?": "OWON,SPM8104,25390427,FV:V1.0.0",
    "CONFigure:ALL?": "CONT,OL,Manual,",
    "MEAS:VOLT?": "3.300",
    "MEAS:CURR?": "0.150",
}


class FakeSerialPort:
    """In-memory :class:`~benchhud.instruments.serial_port.SerialPort`.

    Maps written command lines to canned response lines (a fake SCPI responder).
    Records everything written to ``self.written`` for assertions. Reading with
    no queued data returns ``b""`` — the same "nothing before timeout" signal a
    real port gives.
    """

    def __init__(
        self,
        responses: Mapping[str, str] | None = None,
        *,
        terminator: str = "\n",
        encoding: str = "ascii",
        supply_state: bool = False,
    ) -> None:
        self._responses = dict(responses or {})
        # With supply_state, it remembers VOLT/CURR/OUTP and answers their
        # queries, as the real supply does (#289). Commands whose first word is
        # in ``drop`` are ignored, as the OWON did with back-to-back writes (#280).
        self.state: dict[str, str] | None = (
            {"VOLT": "0.000", "CURR": "0.000", "OUTP": "OFF"} if supply_state else None
        )
        self.drop: set[str] = set()
        self._terminator = terminator
        self._encoding = encoding
        self._rx = bytearray()
        self.written: list[str] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        text = data.decode(self._encoding).rstrip(self._terminator)
        self.written.append(text)
        if self.state is not None and self._supply(text):
            return len(data)
        if text in self._responses:
            reply = self._responses[text] + self._terminator
            self._rx += reply.encode(self._encoding)
        return len(data)

    def _supply(self, text: str) -> bool:
        assert self.state is not None
        key, _, value = text.partition(" ")
        if key.endswith("?") and key[:-1] in self.state:
            self._rx += (self.state[key[:-1]] + self._terminator).encode(self._encoding)
            return True
        if key in self.state and value:
            if key not in self.drop:
                self.state[key] = value
            return True
        return False

    def readline(self) -> bytes:
        term = self._terminator.encode(self._encoding)
        idx = self._rx.find(term)
        if idx == -1:
            out = bytes(self._rx)
            self._rx.clear()
            return out
        end = idx + len(term)
        out = bytes(self._rx[:end])
        del self._rx[:end]
        return out

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep every test off the real user config dir (and its jobs.db)."""
    monkeypatch.setenv("BENCHHUD_CONFIG", str(tmp_path / "config.toml"))


@pytest.fixture
def make_fake_serial() -> Callable[[Mapping[str, str]], FakeSerialPort]:
    """Factory for a :class:`FakeSerialPort` seeded with custom responses."""

    def _make(responses: Mapping[str, str]) -> FakeSerialPort:
        return FakeSerialPort(responses)

    return _make


@pytest.fixture
def fake_owon_serial() -> FakeSerialPort:
    """A fake port that answers like the bench's OWON SPM8104."""
    return FakeSerialPort(OWON_SPM8104_RESPONSES, supply_state=True)
