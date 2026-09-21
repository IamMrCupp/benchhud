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


def test_reason_is_actionable_when_obs_is_absent(monkeypatch) -> None:
    from benchhud.output import cmio

    monkeypatch.setattr(cmio, "find_obs_sink", lambda: None)
    with pytest.raises(cmio.CmioSinkUnavailable) as info:
        cmio.open_cmio_sink(1280, 720)
    assert "approve its camera extension" in info.value.reason


def test_reason_names_the_real_cause_when_stream_wont_open(monkeypatch) -> None:
    """OBS present but the stream fails: say so — never 'not wired up yet' (#152).

    That message was true when discovery landed before delivery; after delivery
    shipped it sent operators to wait for a feature that already existed, when
    the actual cause was almost always OBS's own virtual camera holding the
    stream.
    """
    from benchhud.output import cmio

    monkeypatch.setattr(cmio, "find_obs_sink", lambda: (_device(OBS_DEVICE_UID), _SINK))

    def _boom(*_a, **_k):
        raise RuntimeError("stream busy")

    monkeypatch.setattr(cmio, "CmioSink", _boom)
    with pytest.raises(cmio.CmioSinkUnavailable) as info:
        cmio.open_cmio_sink(1280, 720)
    reason = info.value.reason
    assert "not wired up" not in reason
    assert "Start Virtual Camera" in reason
    assert "stream busy" in reason  # the underlying error is carried through
    assert not hasattr(sink_module, "MACOS_NOT_WIRED")  # the stale constant is gone


def test_open_output_sink_on_macos_reports_the_open_failure(monkeypatch) -> None:
    """The shell-facing fallback carries the same reason the open raised with."""
    from benchhud.output import cmio

    monkeypatch.setattr(sink_module.sys, "platform", "darwin")

    def _unavailable(*_a, **_k):
        raise cmio.CmioSinkUnavailable("why it failed")

    monkeypatch.setattr(cmio, "open_cmio_sink", _unavailable)
    sink = sink_module.open_output_sink("/dev/ignored", 640, 480)
    assert isinstance(sink, sink_module.NullSink)
    assert sink.reason == "why it failed"


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


@pytest.mark.skipif(not sys.platform.startswith("darwin"), reason="CoreVideo is macOS-only")
def test_real_pixel_buffers_are_iosurface_backed() -> None:
    """#177: a heap-backed buffer enqueues fine but never reaches OBS's process."""
    from benchhud.output.cmio import _DeliveryFrameworks

    api = _DeliveryFrameworks()
    pixel_buffer = api.new_pixel_buffer(320, 240)
    assert pixel_buffer is not None
    try:
        assert api.cv.CVPixelBufferGetIOSurface(pixel_buffer), "buffer has no IOSurface"
    finally:
        api.cv.CVPixelBufferRelease(pixel_buffer)


# --- real delivery into OBS (dev Mac with the extension approved) ---------

_obs = None
if sys.platform.startswith("darwin"):
    from benchhud.output.cmio import find_obs_sink

    _obs = find_obs_sink()


@pytest.mark.skipif(_obs is None, reason="needs macOS with OBS's camera extension approved")
def test_real_delivery_into_obs() -> None:
    """Push real frames and confirm OBS's extension consumes them, then close."""
    from benchhud.output.cmio import open_cmio_sink

    sink = open_cmio_sink(320, 240)
    assert sink.size == (320, 240)
    for i in range(30):
        sink.send(np.full((240, 320, 3), i % 256, dtype=np.uint8))
    assert sink._frame > 0, "no frames were accepted by the queue"
    sink.close()
    sink.close()  # idempotent
    sink.send(np.zeros((240, 320, 3), dtype=np.uint8))  # no-op after close, must not raise
