"""Tests for the macOS CoreMediaIO virtual-camera backend.

The pure parts (pixel conversion, stream selection, device matching) run
anywhere; enumeration runs against an injected fake. One smoke test talks to the
real framework and is skipped off macOS.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from benchhud.output import sink as sink_module
from benchhud.output.cmio import (
    OBS_DEVICE_UID,
    CmioDevice,
    CmioStream,
    _fourcc,
    bgr_to_uyvy,
    enumerate_devices,
    find_obs_device,
    select_sink_stream,
)

_SOURCE = CmioStream(stream_id=10, direction=1)  # what apps read (like a camera)
_SINK = CmioStream(stream_id=11, direction=0)  # what producers write into


# --- selectors -----------------------------------------------------------


def test_fourcc_packs_big_endian() -> None:
    assert _fourcc("dev#") == (ord("d") << 24) | (ord("e") << 16) | (ord("v") << 8) | ord("#")


# --- stream selection ----------------------------------------------------


def test_sink_stream_is_chosen_by_direction_not_position() -> None:
    """pyvirtualcam hardcodes streams[1]; we ask which way the stream points."""
    assert select_sink_stream((_SINK, _SOURCE)) is _SINK


def test_sink_stream_found_in_obs_ordering() -> None:
    assert select_sink_stream((_SOURCE, _SINK)) is _SINK


def test_sink_stream_falls_back_to_second_when_direction_unknown() -> None:
    unknown_a = CmioStream(stream_id=1, direction=None)
    unknown_b = CmioStream(stream_id=2, direction=None)
    assert select_sink_stream((unknown_a, unknown_b)) is unknown_b


def test_no_sink_on_a_plain_camera() -> None:
    """A normal capture device has one readable stream and nothing to write to."""
    assert select_sink_stream((_SOURCE,)) is None


def test_no_sink_when_there_are_no_streams() -> None:
    assert select_sink_stream(()) is None


# --- device matching -----------------------------------------------------


def _device(uid: str | None, streams=(_SOURCE, _SINK)) -> CmioDevice:
    return CmioDevice(device_id=1, uid=uid, streams=streams)


def test_finds_obs_by_uid() -> None:
    devices = [_device("6C707041-05AC-0010"), _device(OBS_DEVICE_UID)]
    assert find_obs_device(devices).uid == OBS_DEVICE_UID


def test_uid_match_is_case_insensitive() -> None:
    assert find_obs_device([_device(OBS_DEVICE_UID.lower())]) is not None


def test_no_obs_device_present() -> None:
    assert find_obs_device([_device("something-else"), _device(None)]) is None


# --- pixel conversion ----------------------------------------------------


def test_bgr_to_uyvy_shape_and_dtype() -> None:
    out = bgr_to_uyvy(np.zeros((8, 16, 3), dtype=np.uint8))
    assert out.dtype == np.uint8
    assert out.shape[:2] == (8, 16)


def test_bgr_to_uyvy_crops_odd_width() -> None:
    """UYVY subsamples chroma horizontally, so an odd width can't be packed."""
    out = bgr_to_uyvy(np.zeros((4, 7, 3), dtype=np.uint8))
    assert out.shape[1] == 6


def test_bgr_to_uyvy_preserves_content_distinctly() -> None:
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    frame[:, :, 2] = 255  # red in BGR
    red = bgr_to_uyvy(frame)
    frame[:] = 0
    frame[:, :, 0] = 255  # blue
    assert not np.array_equal(red, bgr_to_uyvy(frame))


# --- enumeration against a fake framework --------------------------------


class _FakeFrameworks:
    """Stands in for CoreMediaIO: two devices, one of them OBS-shaped."""

    def __init__(self) -> None:
        self._streams = {100: [10, 11], 200: [20]}
        self._uids = {100: OBS_DEVICE_UID, 200: "built-in-camera"}
        self._directions = {10: 1, 11: 0, 20: 1}

    def uint32_array(self, obj: int, selector: int) -> list[int]:
        return [100, 200] if obj == 1 else self._streams.get(obj, [])

    def uint32(self, obj: int, selector: int) -> int | None:
        return self._directions.get(obj)

    def string(self, obj: int, selector: int) -> str | None:
        return self._uids.get(obj)


def test_enumerate_builds_devices_with_streams_and_directions() -> None:
    devices = enumerate_devices(_FakeFrameworks())
    assert [d.device_id for d in devices] == [100, 200]
    obs = find_obs_device(devices)
    assert obs is not None
    assert select_sink_stream(obs.streams).stream_id == 11


def test_enumerate_handles_a_device_with_one_stream() -> None:
    camera = enumerate_devices(_FakeFrameworks())[1]
    assert len(camera.streams) == 1
    assert select_sink_stream(camera.streams) is None


# --- reason reported to the operator -------------------------------------


def test_macos_reason_is_actionable_when_obs_is_absent(monkeypatch) -> None:
    monkeypatch.setattr("benchhud.output.cmio.find_obs_sink", lambda: None)
    reason = sink_module._macos_reason()
    assert "approve its camera extension" in reason


def test_macos_reason_differs_once_obs_is_present(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchhud.output.cmio.find_obs_sink", lambda: (_device(OBS_DEVICE_UID), _SINK)
    )
    assert sink_module._macos_reason() == sink_module.MACOS_NOT_WIRED


# --- real framework smoke test (macOS only) ------------------------------


@pytest.mark.skipif(not sys.platform.startswith("darwin"), reason="CoreMediaIO is macOS-only")
def test_real_enumeration_returns_devices_with_uids() -> None:
    """Talks to the actual framework — proves the ctypes bindings are right."""
    devices = enumerate_devices()
    assert devices, "expected at least one camera on a Mac"
    assert any(d.uid for d in devices)
    for device in devices:
        for stream in device.streams:
            assert stream.direction in (0, 1, None)
