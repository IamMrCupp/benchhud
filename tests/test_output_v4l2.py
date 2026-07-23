"""Tests for the v4l2loopback output sink.

The struct layout is the contract with the kernel, so it's asserted at the byte
level — that check is the whole reason this can be written off the bench.
"""

from __future__ import annotations

import os
import struct
import threading

import numpy as np
import pytest

from benchhud.output.sink import OutputSink
from benchhud.output.v4l2 import (
    V4L2_BUF_TYPE_VIDEO_OUTPUT,
    V4L2_FIELD_NONE,
    V4L2_FORMAT_SIZE,
    V4L2_PIX_FMT_RGB24,
    VIDIOC_S_FMT,
    V4l2LoopbackSink,
    _LoopbackFd,
    pack_v4l2_format,
    unpack_v4l2_format,
)


class _FakeDevice:
    """Stand-in for an opened loopback node: records the bytes written to it."""

    def __init__(self, device: str = "/dev/video10", width: int = 2, height: int = 2):
        self.device = device
        self.size = (width, height)
        self.writes: list[bytes] = []
        self.close_count = 0

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def close(self) -> None:
        self.close_count += 1


def _sink_with_fake(width: int = 2, height: int = 2) -> tuple[V4l2LoopbackSink, _FakeDevice]:
    device = _FakeDevice(width=width, height=height)
    sink = V4l2LoopbackSink("/dev/video10", width, height, device_factory=lambda d, w, h: device)
    return sink, device


# --- struct layout ----------------------------------------------------------


def test_format_struct_is_the_size_the_ioctl_encodes() -> None:
    """VIDIOC_S_FMT encodes sizeof(struct v4l2_format) — they must agree."""
    assert len(pack_v4l2_format(640, 480)) == V4L2_FORMAT_SIZE
    assert (VIDIOC_S_FMT >> 16) & 0x3FFF == V4L2_FORMAT_SIZE


def test_vidioc_s_fmt_matches_the_kernel_encoding() -> None:
    """_IOWR('V', 5, struct v4l2_format) on a 64-bit kernel."""
    assert VIDIOC_S_FMT == 0xC0D05605


def test_rgb24_fourcc() -> None:
    assert int.from_bytes(b"RGB3", "little") == V4L2_PIX_FMT_RGB24


def test_pack_places_fields_at_the_kernel_offsets() -> None:
    payload = pack_v4l2_format(640, 480)

    # type at 0, then 4 bytes of union alignment padding.
    (buf_type,) = struct.unpack_from("=I", payload, 0)
    assert buf_type == V4L2_BUF_TYPE_VIDEO_OUTPUT
    assert payload[4:8] == b"\x00\x00\x00\x00"

    # v4l2_pix_format begins at offset 8.
    width, height, pixelformat, field, bytesperline, sizeimage = struct.unpack_from(
        "=6I", payload, 8
    )
    assert (width, height) == (640, 480)
    assert pixelformat == V4L2_PIX_FMT_RGB24
    assert field == V4L2_FIELD_NONE
    assert bytesperline == 640 * 3
    assert sizeimage == 640 * 480 * 3


def test_pack_rejects_a_degenerate_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        pack_v4l2_format(0, 480)


def test_unpack_round_trips_pack() -> None:
    assert unpack_v4l2_format(pack_v4l2_format(1280, 720)) == (
        1280,
        720,
        V4L2_PIX_FMT_RGB24,
    )


def test_unpack_reads_a_driver_adjusted_format() -> None:
    """What open_loopback_device checks for: the driver handing back its own size."""
    adjusted = bytearray(pack_v4l2_format(1280, 720))
    struct.pack_into("=2I", adjusted, 8, 640, 480)

    assert unpack_v4l2_format(bytes(adjusted))[:2] == (640, 480)


def test_unpack_rejects_a_short_payload() -> None:
    with pytest.raises(ValueError, match="too short"):
        unpack_v4l2_format(b"\x00" * 16)


# --- sink behavior ----------------------------------------------------------


def test_sink_satisfies_protocol() -> None:
    sink, _ = _sink_with_fake()
    assert isinstance(sink, OutputSink)
    assert sink.size == (2, 2)
    assert sink.device == "/dev/video10"


def test_sink_writes_rgb_bytes() -> None:
    sink, device = _sink_with_fake()

    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # pure blue in BGR
    sink.send(frame)

    assert len(device.writes) == 1
    written = device.writes[0]
    assert len(written) == 2 * 2 * 3
    assert written[:3] == b"\x00\x00\xff"  # -> RGB


def test_sink_writes_rows_in_order() -> None:
    """Row-major, top row first — a flipped feed is a subtle, ugly bug."""
    sink, device = _sink_with_fake(width=1, height=2)

    frame = np.zeros((2, 1, 3), dtype=np.uint8)
    frame[0, 0] = (0, 0, 10)  # BGR red-ish, top row
    frame[1, 0] = (20, 0, 0)  # BGR blue-ish, bottom row
    sink.send(frame)

    assert device.writes[0] == bytes([10, 0, 0, 0, 0, 20])


def test_sink_rejects_a_mis_sized_frame() -> None:
    """A short write would desync the stream instead of failing visibly."""
    sink, _ = _sink_with_fake(width=4, height=4)

    with pytest.raises(ValueError, match="sink expects 4x4"):
        sink.send(np.zeros((2, 2, 3), dtype=np.uint8))


def test_sink_rejects_a_non_bgr_frame() -> None:
    sink, _ = _sink_with_fake()

    with pytest.raises(ValueError, match="3-channel"):
        sink.send(np.zeros((2, 2), dtype=np.uint8))


def test_sink_close_closes_the_device_once() -> None:
    sink, device = _sink_with_fake()

    sink.close()
    sink.close()  # idempotent — the shell closes on both stop and quit

    assert device.close_count == 1


def test_sink_send_after_close_raises() -> None:
    sink, _ = _sink_with_fake()
    sink.close()

    with pytest.raises(RuntimeError, match="closed"):
        sink.send(np.zeros((2, 2, 3), dtype=np.uint8))


# --- file descriptor --------------------------------------------------------


def test_loopback_fd_writes_whole_frames_through_short_writes() -> None:
    """A pipe forces the partial writes a real device can also hand back."""
    read_fd, write_fd = os.pipe()
    payload = b"\xa5" * (1024 * 256)  # larger than the default pipe buffer

    def _drain() -> None:
        got = bytearray()
        while len(got) < len(payload):
            got += os.read(read_fd, 65536)
        assert bytes(got) == payload

    reader = threading.Thread(target=_drain)
    reader.start()
    try:
        _LoopbackFd(write_fd).write(payload)
    finally:
        reader.join(timeout=5)
        os.close(read_fd)

    assert not reader.is_alive()


def test_loopback_fd_close_releases_the_descriptor() -> None:
    read_fd, write_fd = os.pipe()
    os.close(read_fd)

    _LoopbackFd(write_fd).close()

    with pytest.raises(OSError):
        os.write(write_fd, b"x")
