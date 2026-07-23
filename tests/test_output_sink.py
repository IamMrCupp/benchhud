"""Tests for output sinks."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.capture.frame_source import SyntheticFrameSource
from benchhud.output.sink import FakeSink, NullSink, OutputSink, open_output_sink


def test_fake_sink_satisfies_protocol() -> None:
    assert isinstance(FakeSink(), OutputSink)


def test_fake_sink_records_frames() -> None:
    sink = FakeSink(64, 48)
    assert sink.size == (64, 48)
    sink.send(SyntheticFrameSource(64, 48).read())
    assert sink.frame_count == 1


def test_fake_sink_send_after_close_raises() -> None:
    sink = FakeSink()
    sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        sink.send(np.zeros((4, 4, 3), dtype=np.uint8))


def test_null_sink_satisfies_protocol() -> None:
    assert isinstance(NullSink(), OutputSink)


def test_null_sink_counts_without_retaining() -> None:
    """The live fallback runs for hours — it must not accumulate frames."""
    sink = NullSink(64, 48)
    for _ in range(3):
        sink.send(np.zeros((48, 64, 3), dtype=np.uint8))

    assert sink.frame_count == 3
    assert not any(isinstance(v, list) for v in vars(sink).values())


def test_open_output_sink_falls_back_when_device_missing() -> None:
    """No v4l2loopback (every dev machine, and CI) must not break the HUD."""
    sink = open_output_sink("/dev/does-not-exist", 64, 48)

    assert isinstance(sink, NullSink)
    assert sink.size == (64, 48)


def test_frame_flows_source_to_sink() -> None:
    """AT3 (hardware-free): a frame flows source -> sink."""
    source = SyntheticFrameSource(32, 32)
    sink = FakeSink(32, 32)
    for _ in range(3):
        frame = source.read()
        assert frame is not None
        sink.send(frame)
    assert sink.frame_count == 3
