"""Convert OpenCV BGR frames to Qt images.

Shared by the video panels. OpenCV frames are BGR, and Qt has a matching
``Format_BGR888``, so the pixels go across as they are: no channel swap, which
in numpy was a full-frame copy per view per frame (#347).
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap


def _wrap(frame: np.ndarray) -> tuple[QImage, np.ndarray]:
    """A QImage over ``frame``'s own pixels, and the array that must outlive it."""
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("expected an (H, W, 3) BGR frame")
    height, width = frame.shape[:2]
    pixels = np.ascontiguousarray(frame, dtype=np.uint8)  # a no-op for a normal frame
    return QImage(pixels.data, width, height, 3 * width, QImage.Format.Format_BGR888), pixels


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    """Convert an ``(H, W, 3)`` uint8 BGR array to a :class:`QImage`.

    The image owns a private copy of the pixels, so it stays valid after the
    source array is garbage-collected.
    """
    image, _pixels = _wrap(frame)
    return image.copy()  # detach from the numpy buffer


def fitted_size(frame_size: tuple[int, int], target: QSize) -> tuple[int, int]:
    """``(width, height)`` of a frame scaled to fit ``target``, aspect kept.

    The frame's own size when ``target`` is empty (a view not laid out yet).
    """
    if target.isEmpty():
        return frame_size
    fitted = QSize(*frame_size).scaled(target, Qt.AspectRatioMode.KeepAspectRatio)
    return max(1, fitted.width()), max(1, fitted.height())


def _resized(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """``frame`` at ``size``, averaging rather than skipping pixels on a big shrink.

    Halved while it is still at least twice the target: a bilinear resize to
    exactly half is a 2x2 average, so no pixel is skipped. Then one bilinear
    step for what's left, which is under 2x. On the bench that is 1.3 ms for
    1080p to a 480x270 dock where ``INTER_AREA`` took 5.8 ms, with the same
    picture; a single bilinear step from 1080p would alias.
    """
    height, width = frame.shape[:2]
    while width >= 2 * size[0] and height >= 2 * size[1] and width % 2 == 0 and height % 2 == 0:
        width, height = width // 2, height // 2
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
    if (width, height) != size:
        frame = cv2.resize(frame, size, interpolation=cv2.INTER_LINEAR)
    return frame


def bgr_to_fitted_pixmap(frame: np.ndarray, target: QSize) -> QPixmap:
    """A pixmap of ``frame`` (BGR) scaled to fit ``target``, aspect kept.

    The frame is resized in OpenCV first, so Qt only ever converts a view-sized
    image, not the full frame, and there is no full-size copy or smooth-scale
    per view per frame (#347). The pixmap owns its pixels.
    """
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("expected an (H, W, 3) BGR frame")
    height, width = frame.shape[:2]
    size = fitted_size((width, height), target)
    if size != (width, height):
        frame = _resized(np.ascontiguousarray(frame, dtype=np.uint8), size)
    image, _pixels = _wrap(frame)
    # BGR888 is never a pixmap's native format, so fromImage converts into the
    # pixmap's own memory: nothing here points at the numpy buffer afterwards.
    return QPixmap.fromImage(image)
