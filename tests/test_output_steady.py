"""Tests for the steady loopback writer (#566)."""

from __future__ import annotations

import logging
import threading
import time
from itertools import pairwise

import numpy as np
import pytest

from benchhud.output.sink import FakeSink, OutputSink, open_output_sink
from benchhud.output.steady import SteadySink

W, H = 8, 6


class _Recorder:
    """An inner sink that records what was written and when."""

    def __init__(
        self,
        *,
        block: float = 0.0,
        block_every: bool = False,
        fail_on: int = 0,
        hold: threading.Event | None = None,
    ) -> None:
        self.size = (W, H)
        self.writes: list[tuple[float, bytes]] = []
        self.closed = 0
        self.writing = threading.Event()
        self._block = block
        self._block_every = block_every
        self._fail_on = fail_on
        self._hold = hold
        self._cond = threading.Condition()

    def send(self, frame: np.ndarray) -> None:
        if self._fail_on and len(self.writes) + 1 == self._fail_on:
            raise OSError("loopback went away")
        if self._block and (self._block_every or len(self.writes) == 1):
            time.sleep(self._block)  # the second write stalls (or every one after the first)
        if self._hold is not None and self.writes:
            self.writing.set()
            self._hold.wait()  # the second write hangs until it's let go
        with self._cond:
            self.writes.append((time.monotonic(), frame.tobytes()))
            self._cond.notify_all()

    def close(self) -> None:
        self.closed += 1

    def wait_for(self, count: int, timeout: float = 2.0) -> None:
        with self._cond:
            assert self._cond.wait_for(lambda: len(self.writes) >= count, timeout), (
                f"{len(self.writes)} writes, wanted {count}"
            )


def _frame(value: int) -> np.ndarray:
    return np.full((H, W, 3), value, dtype=np.uint8)


@pytest.fixture
def sinks():
    made: list[SteadySink] = []

    def make(inner, **kwargs) -> SteadySink:
        sink = SteadySink(inner, name="test", **kwargs)
        made.append(sink)
        return sink

    yield make
    for sink in made:
        sink.close()


def test_satisfies_the_protocol(sinks) -> None:
    sink = sinks(FakeSink(W, H))
    assert isinstance(sink, OutputSink)
    assert sink.size == (W, H)


def test_new_frames_are_written_in_order(sinks) -> None:
    inner = _Recorder()
    sink = sinks(inner, repeat_after=5.0)
    for value in (1, 2, 3):
        sink.send(_frame(value))
        inner.wait_for(value)

    assert [data[0] for _, data in inner.writes] == [1, 2, 3]


def test_a_caller_that_stops_sending_still_gets_frames_written(sinks) -> None:
    """The GUI thread stalls: the last frame keeps going out on time."""
    inner = _Recorder()
    sink = sinks(inner, repeat_after=0.03)
    sink.send(_frame(7))
    inner.wait_for(6)

    times = [t for t, _ in inner.writes]
    assert all(data == _frame(7).tobytes() for _, data in inner.writes)
    assert max(b - a for a, b in pairwise(times)) < 1.0
    assert sink.repeats >= 5


def test_nothing_is_written_before_the_first_frame(sinks) -> None:
    inner = _Recorder()
    sinks(inner, repeat_after=0.01)
    time.sleep(0.1)
    assert inner.writes == []


def test_what_goes_out_is_the_frame_as_it_was_when_sent(sinks) -> None:
    """A repeat goes out later; changing the array afterwards can't reach it."""
    inner = _Recorder()
    sink = sinks(inner, repeat_after=0.02)
    frame = _frame(1)
    sink.send(frame)
    frame[:] = 255  # the caller keeps using its array
    inner.wait_for(4)

    assert all(data == _frame(1).tobytes() for _, data in inner.writes)


def test_a_mis_sized_frame_is_refused_on_the_callers_thread(sinks) -> None:
    sink = sinks(_Recorder())
    with pytest.raises(ValueError, match="sink expects"):
        sink.send(np.zeros((H, W + 2, 3), dtype=np.uint8))


def test_a_failed_write_is_raised_by_the_next_send(sinks) -> None:
    inner = _Recorder(fail_on=2)
    sink = sinks(inner, repeat_after=0.01)
    sink.send(_frame(1))
    deadline = time.monotonic() + 2
    with pytest.raises(OSError, match="went away"):
        while time.monotonic() < deadline:
            sink.send(_frame(2))
            time.sleep(0.01)


def test_close_stops_the_writer_and_closes_the_sink_once(sinks) -> None:
    inner = _Recorder()
    sink = sinks(inner, repeat_after=0.01)
    sink.send(_frame(1))
    inner.wait_for(2)
    sink.close()
    sink.close()
    written = len(inner.writes)
    time.sleep(0.05)

    assert inner.closed == 1
    assert len(inner.writes) == written
    with pytest.raises(RuntimeError, match="closed"):
        sink.send(_frame(2))


def test_a_long_wait_for_a_new_frame_is_logged_when_it_ends(sinks, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.output.steady")
    inner = _Recorder()
    sink = sinks(inner, repeat_after=0.02, gap_log=0.15)
    sink.send(_frame(1))
    inner.wait_for(1)
    time.sleep(0.25)
    assert "no new frame" not in caplog.text  # said when it ends, not while
    sink.send(_frame(2))
    inner.wait_for(len(inner.writes) + 1)

    lines = [r.getMessage() for r in caplog.records if "no new frame" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].startswith("test: no new frame for ")
    assert "more times meanwhile" in lines[0]


def test_the_first_frame_after_start_is_not_a_gap(sinks, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.output.steady")
    inner = _Recorder()
    sink = sinks(inner, repeat_after=5.0, gap_log=0.05)
    time.sleep(0.1)
    sink.send(_frame(1))
    inner.wait_for(1)

    assert caplog.records == []


def test_a_stalled_write_is_logged(sinks, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.output.steady")
    inner = _Recorder(block=0.2)
    sink = sinks(inner, repeat_after=0.02, write_gap_log=0.15)
    sink.send(_frame(1))
    inner.wait_for(3)

    assert any("between writes" in r.getMessage() for r in caplog.records)


def test_a_short_gap_between_writes_is_logged_too(sinks, caplog) -> None:
    """OBS times out after 166 ms; a gap that long is worth a line, not just a second's."""
    caplog.set_level(logging.WARNING, logger="benchhud.output.steady")
    inner = _Recorder(block=0.2)
    sinks(inner, repeat_after=0.02).send(_frame(1))
    inner.wait_for(3)

    assert any("between writes" in r.getMessage() for r in caplog.records)


def test_gaps_between_writes_are_rate_limited_and_counted(sinks, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.output.steady")
    inner = _Recorder(block=0.06, block_every=True)
    sink = sinks(inner, repeat_after=0.01, write_gap_log=0.05, write_gap_log_every=0.3)
    sink.send(_frame(1))
    inner.wait_for(12)

    lines = [r.getMessage() for r in caplog.records if "between writes" in r.getMessage()]
    assert 1 <= len(lines) < len(inner.writes) - 3
    assert any("more since the last line, the longest" in line for line in lines[1:])


def test_a_failed_write_is_logged_on_the_writer_thread(sinks, caplog) -> None:
    """Logged even if nothing is ever sent again to raise it."""
    caplog.set_level(logging.WARNING, logger="benchhud.output.steady")
    inner = _Recorder(fail_on=1)
    sinks(inner).send(_frame(1))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "write failed" not in caplog.text:
        time.sleep(0.01)

    assert "test: write failed, the writer stopped: loopback went away" in caplog.text


def test_close_leaves_a_hung_write_its_device_until_it_returns(sinks, caplog) -> None:
    """Closing the fd under a write in progress could tear a frame into the next open."""
    caplog.set_level(logging.WARNING, logger="benchhud.output.steady")
    hold = threading.Event()
    inner = _Recorder(hold=hold)
    sink = sinks(inner, repeat_after=0.01, close_wait=0.05)
    sink.send(_frame(1))
    assert inner.writing.wait(2)

    sink.close()
    assert inner.closed == 0
    assert "still running" in caplog.text

    hold.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not inner.closed:
        time.sleep(0.01)
    time.sleep(0.05)
    assert inner.closed == 1


def test_the_linux_loopback_is_written_on_its_own_thread(monkeypatch) -> None:
    monkeypatch.setattr("benchhud.output.sink.sys.platform", "linux")
    monkeypatch.setattr(
        "benchhud.output.sink.V4l2LoopbackSink", lambda device, w, h: FakeSink(w, h)
    )
    sink = open_output_sink("/dev/video10", W, H)
    try:
        assert isinstance(sink, SteadySink)
        assert isinstance(sink.inner, FakeSink)
        assert sink.size == (W, H)
    finally:
        sink.close()
    assert sink.inner.closed
