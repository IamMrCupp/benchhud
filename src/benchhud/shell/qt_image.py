"""Convert OpenCV BGR frames to Qt images.

Shared by the video panels. OpenCV frames are BGR, and Qt has a matching
``Format_BGR888``, so the pixels go across as they are: no channel swap, which
in numpy was a full-frame copy per view per frame (#347). The returned QImage
owns a private copy of the pixels, so it stays valid after the source array is
garbage-collected.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    """Convert an ``(H, W, 3)`` uint8 BGR array to a :class:`QImage`."""
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("expected an (H, W, 3) BGR frame")
    height, width = frame.shape[:2]
    pixels = np.ascontiguousarray(frame)  # a no-op for a normal frame
    image = QImage(pixels.data, width, height, 3 * width, QImage.Format.Format_BGR888)
    return image.copy()  # detach from the numpy buffer
