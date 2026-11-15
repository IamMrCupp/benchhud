"""Write composited frames to a v4l2loopback device.

This is the Linux virtual-camera output: open ``/dev/videoN`` (a loopback node
created by the ``v4l2loopback`` kernel module), declare the frame format with a
``VIDIOC_S_FMT`` ioctl, then write raw 24-bit frames to the file descriptor.
OBS — or anything else that reads a V4L2 capture device — sees a camera.

The node is asked for BGR24 first, which is the byte order OpenCV frames
already have, so a frame goes out as it is. A node that answers RGB24 instead
gets the channels swapped on the way out (#347).

Only the kernel's uapi contract is used here (stdlib ``os``/``fcntl``/``struct``,
no third-party library), which is what lets benchhud's licensing stay open.

The device can only be opened on a machine with the module loaded (the Linux
bench), so the struct pack/unpack pair is pure and tested byte-for-byte on any
OS, and :class:`V4l2LoopbackSink` takes an injectable ``device_factory`` — frame
handling is unit-tested with a fake device and only the real write is
bench-gated.
"""

from __future__ import annotations

import logging
import os
import struct
from collections.abc import Callable
from typing import Protocol

import cv2
import numpy as np

log = logging.getLogger(__name__)

# --- linux/videodev2.h ------------------------------------------------------

V4L2_BUF_TYPE_VIDEO_OUTPUT = 2
V4L2_FIELD_NONE = 1
V4L2_COLORSPACE_SRGB = 8

#: ``v4l2_fourcc('R', 'G', 'B', '3')`` — 24 bits per pixel, R then G then B.
V4L2_PIX_FMT_RGB24 = ord("R") | (ord("G") << 8) | (ord("B") << 16) | (ord("3") << 24)

#: ``v4l2_fourcc('B', 'G', 'R', '3')`` — 24 bits per pixel, B then G then R: an
#: OpenCV frame's own layout.
V4L2_PIX_FMT_BGR24 = ord("B") | (ord("G") << 8) | (ord("R") << 16) | (ord("3") << 24)

#: The formats the sink can write, in the order the node is asked for them.
_WRITABLE_FORMATS = (V4L2_PIX_FMT_BGR24, V4L2_PIX_FMT_RGB24)

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


def pack_v4l2_format(width: int, height: int, pixelformat: int = V4L2_PIX_FMT_RGB24) -> bytes:
    """Build the ``struct v4l2_format`` payload for a 24-bit output stream.

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
        pixelformat,
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

    #: The fourcc the node agreed to: BGR24 or RGB24.
    pixelformat: int

    def write(self, data: bytes | memoryview) -> None:
        """Write one whole frame."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...


#: Takes ``(device, width, height)`` and returns an opened :class:`V4l2Device`.
DeviceFactory = Callable[[str, int, int], V4l2Device]


class V4l2LoopbackSink:
    """An :class:`~benchhud.output.sink.OutputSink` backed by v4l2loopback.

    Frames arrive from OpenCV as BGR. A node opened as BGR24 takes them as
    they are; one opened as RGB24 gets the channels swapped on the way out. The
    frame size is fixed when the device is opened — a mis-sized frame would
    desync the stream rather than fail visibly, so it is rejected instead.
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
        if self._device.pixelformat not in _WRITABLE_FORMATS:
            opened, self._device = self._device, None
            opened.close()
            raise RuntimeError(
                f"{device} opened as fourcc {opened.pixelformat:#x}, not BGR24 or RGB24"
            )
        self._swap = self._device.pixelformat != V4L2_PIX_FMT_BGR24

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def device(self) -> str:
        return self._device_path

    @property
    def swaps_channels(self) -> bool:
        """Whether each frame is converted to RGB (the node didn't take BGR24)."""
        return self._swap

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

        # Written straight from the array, no tobytes(). A BGR24 node takes the
        # frame's own bytes; only an RGB24 one costs a swap (OpenCV's, SIMD) (#347).
        pixels = np.ascontiguousarray(frame, dtype=np.uint8)
        if self._swap:
            pixels = cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)
        self._device.write(memoryview(pixels).cast("B"))

    def close(self) -> None:
        if self._device is None:
            return
        self._device.close()
        self._device = None


class _LoopbackFd:
    """A raw file descriptor that writes whole frames."""

    def __init__(self, fd: int, pixelformat: int = V4L2_PIX_FMT_RGB24) -> None:
        self._fd = fd
        self.pixelformat = pixelformat

    def write(self, data: bytes | memoryview) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self._fd, view) :]

    def close(self) -> None:
        os.close(self._fd)


def open_loopback_device(device: str, width: int, height: int) -> V4l2Device:
    """Open a real loopback node and declare the output format.

    BGR24 is asked for first, then RGB24. ``VIDIOC_S_FMT`` hands back the format
    the node settled on, which can differ from the one asked for (a node whose
    format is pinned, or held by a reader, keeps its own), so the answer decides
    what gets written, not the request.

    Raises ``OSError`` if the node is missing, is not a loopback device, or is
    already held by another writer, and ``RuntimeError`` if the driver adjusted
    the size we asked for or offers neither format. Callers that must stay up
    should fall back — see :func:`benchhud.output.sink.open_output_sink`.
    """
    import fcntl  # Unix-only; keeps this module importable on Windows.

    fd = os.open(device, os.O_WRONLY)
    try:
        got_format = 0
        for wanted in _WRITABLE_FORMATS:
            granted = fcntl.ioctl(fd, VIDIOC_S_FMT, pack_v4l2_format(width, height, wanted))
            got_width, got_height, got_format = unpack_v4l2_format(granted)
            if (got_width, got_height) != (width, height):
                raise RuntimeError(
                    f"{device} adjusted the format to {got_width}x{got_height}, "
                    f"asked for {width}x{height}"
                )
            if got_format in _WRITABLE_FORMATS:
                break
        else:
            raise RuntimeError(f"{device} refused BGR24 and RGB24 (got fourcc {got_format:#x})")
    except BaseException:
        os.close(fd)
        raise
    name = "BGR24" if got_format == V4L2_PIX_FMT_BGR24 else "RGB24 (channels swapped per frame)"
    log.info("%s opened at %dx%d as %s", device, width, height, name)
    return _LoopbackFd(fd, got_format)
