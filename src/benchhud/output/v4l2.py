"""Write composited frames to a v4l2loopback device.

This is the Linux virtual-camera output: open ``/dev/videoN`` (a loopback node
created by the ``v4l2loopback`` kernel module), declare the frame format with a
``VIDIOC_S_FMT`` ioctl, then write raw RGB24 frames to the file descriptor.
OBS — or anything else that reads a V4L2 capture device — sees a camera.

Only the kernel's uapi contract is used here (stdlib ``os``/``fcntl``/``struct``,
no third-party library), which is what lets benchhud's licensing stay open.

The device can only be opened on a machine with the module loaded (the Linux
bench), so the struct pack/unpack pair is pure and tested byte-for-byte on any
OS, and :class:`V4l2LoopbackSink` takes an injectable ``device_factory`` — frame
handling is unit-tested with a fake device and only the real write is
bench-gated.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable
from typing import Protocol

import numpy as np

# --- linux/videodev2.h ------------------------------------------------------

V4L2_BUF_TYPE_VIDEO_OUTPUT = 2
V4L2_FIELD_NONE = 1
V4L2_COLORSPACE_SRGB = 8

#: ``v4l2_fourcc('R', 'G', 'B', '3')`` — 24 bits per pixel, R then G then B.
V4L2_PIX_FMT_RGB24 = ord("R") | (ord("G") << 8) | (ord("B") << 16) | (ord("3") << 24)

#: ``sizeof(struct v4l2_format)``: a u32 ``type``, then the ``fmt`` union at
#: offset 8 (the union is 8-aligned — ``v4l2_window`` holds pointers) with a
#: 200-byte ``raw_data`` member.
V4L2_FORMAT_SIZE = 208

#: ``_IOWR('V', 5, struct v4l2_format)`` — ``(dir << 30) | (size << 16) |
#: (type << 8) | nr``, with ``dir`` = read|write = 3.
VIDIOC_S_FMT = (3 << 30) | (V4L2_FORMAT_SIZE << 16) | (ord("V") << 8) | 5

#: ``struct v4l2_format`` through ``v4l2_pix_format.xfer_func``: the buffer type,
#: four bytes of union alignment padding, then twelve u32 pixel-format fields.
_FORMAT_STRUCT = "=I4x12I"

BYTES_PER_PIXEL = 3


def pack_v4l2_format(width: int, height: int) -> bytes:
    """Build the ``struct v4l2_format`` payload for an RGB24 output stream.

    Native byte order with explicit padding — the kernel reads this as a C
    struct, so the layout has to match ``linux/videodev2.h`` exactly.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"format must be positive, got {width}x{height}")

    bytes_per_line = width * BYTES_PER_PIXEL
    header = struct.pack(
        _FORMAT_STRUCT,
        V4L2_BUF_TYPE_VIDEO_OUTPUT,
        width,
        height,
        V4L2_PIX_FMT_RGB24,
        V4L2_FIELD_NONE,
        bytes_per_line,
        bytes_per_line * height,  # sizeimage
        V4L2_COLORSPACE_SRGB,
        0,  # priv
        0,  # flags
        0,  # ycbcr_enc
        0,  # quantization
        0,  # xfer_func
    )
    return header.ljust(V4L2_FORMAT_SIZE, b"\x00")


def unpack_v4l2_format(payload: bytes) -> tuple[int, int, int]:
    """Read ``(width, height, pixelformat)`` back out of a format struct.

    ``VIDIOC_S_FMT`` is a read/write ioctl: the driver may *adjust* what it was
    asked for and hand the corrected format back. Silently writing frames at the
    size we asked for would then desync the stream, so callers check.
    """
    if len(payload) < struct.calcsize(_FORMAT_STRUCT):
        raise ValueError(f"format payload is {len(payload)} bytes, too short")

    _, width, height, pixelformat, *_ = struct.unpack_from(_FORMAT_STRUCT, payload)
    return width, height, pixelformat


class V4l2Device(Protocol):
    """An opened loopback node the sink writes frames to."""

    def write(self, data: bytes) -> None:
        """Write one whole frame."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...


#: Takes ``(device, width, height)`` and returns an opened :class:`V4l2Device`.
DeviceFactory = Callable[[str, int, int], V4l2Device]


class V4l2LoopbackSink:
    """An :class:`~benchhud.output.sink.OutputSink` backed by v4l2loopback.

    Frames arrive from OpenCV as BGR; V4L2 wants RGB24, so channels are swapped
    on the way out. The frame size is fixed when the device is opened — a
    mis-sized frame would desync the stream rather than fail visibly, so it is
    rejected instead.
    """

    def __init__(
        self,
        device: str = "/dev/video10",
        width: int = 1280,
        height: int = 720,
        *,
        device_factory: DeviceFactory | None = None,
    ) -> None:
        self._size = (width, height)
        self._device_path = device
        factory = device_factory or open_loopback_device
        self._device: V4l2Device | None = factory(device, width, height)

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def device(self) -> str:
        return self._device_path

    def send(self, frame: np.ndarray) -> None:
        if self._device is None:
            raise RuntimeError("sink is closed")

        width, height = self._size
        if frame.ndim != 3 or frame.shape[2] != BYTES_PER_PIXEL:
            raise ValueError(f"expected a 3-channel BGR frame, got {frame.shape}")
        if frame.shape[:2] != (height, width):
            raise ValueError(
                f"frame is {frame.shape[1]}x{frame.shape[0]}, sink expects {width}x{height}"
            )

        rgb = np.ascontiguousarray(frame[:, :, ::-1], dtype=np.uint8)  # BGR -> RGB
        self._device.write(rgb.tobytes())

    def close(self) -> None:
        if self._device is None:
            return
        self._device.close()
        self._device = None


class _LoopbackFd:
    """A raw file descriptor that writes whole frames."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self._fd, view) :]

    def close(self) -> None:
        os.close(self._fd)


def open_loopback_device(device: str, width: int, height: int) -> V4l2Device:
    """Open a real loopback node and declare the RGB24 output format.

    Raises ``OSError`` if the node is missing, is not a loopback device, or is
    already held by another writer, and ``RuntimeError`` if the driver adjusted
    the format we asked for. Callers that must stay up should fall back — see
    :func:`benchhud.output.sink.open_output_sink`.
    """
    import fcntl  # Unix-only; keeps this module importable on Windows.

    fd = os.open(device, os.O_WRONLY)
    try:
        granted = fcntl.ioctl(fd, VIDIOC_S_FMT, pack_v4l2_format(width, height))
        got_width, got_height, got_format = unpack_v4l2_format(granted)
        if (got_width, got_height) != (width, height):
            raise RuntimeError(
                f"{device} adjusted the format to {got_width}x{got_height}, "
                f"asked for {width}x{height}"
            )
        if got_format != V4L2_PIX_FMT_RGB24:
            raise RuntimeError(f"{device} refused RGB24 (got fourcc {got_format:#x})")
    except BaseException:
        os.close(fd)
        raise
    return _LoopbackFd(fd)
