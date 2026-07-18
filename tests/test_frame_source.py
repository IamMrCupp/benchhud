"""Tests for frame sources."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.capture.frame_source import FrameSource, SyntheticFrameSource


def test_synthetic_satisfies_protocol() -> None:
    assert isinstance(SyntheticFrameSource(), FrameSource)


def test_size_reported() -> None:
    src = SyntheticFrameSource(320, 240)
    assert src.size == (320, 240)


def test_frame_shape_and_dtype() -> None:
    src = SyntheticFrameSource(320, 240)
    frame = src.read()
    assert frame is not None
    assert frame.shape == (240, 320, 3)
    assert frame.dtype == np.uint8


def test_frames_advance_and_differ() -> None:
    src = SyntheticFrameSource(64, 48)
    first = src.read()
    second = src.read()
    assert src.frame_index == 2
    assert first is not None and second is not None
    assert not np.array_equal(first, second)  # moving bar => distinct frames


def test_read_after_close_returns_none() -> None:
    src = SyntheticFrameSource()
    src.close()
    assert src.read() is None


def test_invalid_size_raises() -> None:
    with pytest.raises(ValueError, match="positive"):
        SyntheticFrameSource(0, 100)
