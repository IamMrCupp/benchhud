"""Tests for the FramePump and the assembled hardware-free pipeline."""

from __future__ import annotations

import time

from benchhud.capture.frame_source import SyntheticFrameSource
from benchhud.capture.latest_frame_reader import LatestFrameReader
from benchhud.capture.pipeline import FramePump
from benchhud.output.sink import FakeSink
from benchhud.shell.panels.scope import ScopePanel


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_tick_returns_false_when_no_frame() -> None:
    reader = LatestFrameReader(SyntheticFrameSource())  # not started
    pump = FramePump(reader)
    assert pump.tick() is False
    assert pump.frames_dispatched == 0


def test_tick_dispatches_to_consumers() -> None:
    received: list = []
    reader = LatestFrameReader(SyntheticFrameSource(32, 32), poll_interval=0.001)
    pump = FramePump(reader, consumers=[received.append])
    pump.start()
    try:
        assert _wait_until(lambda: reader.latest() is not None)
        assert pump.tick() is True
    finally:
        pump.stop()
    assert len(received) == 1
    assert pump.frames_dispatched == 1


def test_end_to_end_source_reader_pump_to_panel_and_sink(qtbot) -> None:
    """Closes hardware-free acceptance Tests 1 & 3: source → reader → pump → panel + sink."""
    panel = ScopePanel()
    qtbot.addWidget(panel)
    sink = FakeSink(64, 48)
    reader = LatestFrameReader(SyntheticFrameSource(64, 48), poll_interval=0.001)
    pump = FramePump(reader, consumers=[panel.update_frame, sink.send])
    pump.start()
    try:
        assert _wait_until(lambda: reader.latest() is not None)
        assert pump.tick() is True
    finally:
        pump.stop()
    assert panel.has_frame is True  # AT1: reached the scope panel
    assert sink.frame_count == 1  # AT3: reached the sink
