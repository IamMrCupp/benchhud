"""Convert OpenCV BGR frames to Qt images.

Shared by the video panels (scope now, thermal later). OpenCV frames are BGR;
QImage's ``Format_RGB888`` expects RGB, so we swap channels. The returned QImage
owns a private copy of the pixels, so it stays valid after the source array is
garbage-collected.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage


def bgr_to_qimage(frame: np.ndarray) -> QImage:
    """Convert an ``(H, W, 3)`` uint8 BGR array to an RGB :class:`QImage`."""
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("expected an (H, W, 3) BGR frame")
    height, width = frame.shape[:2]
    rgb = np.ascontiguousarray(frame[:, :, ::-1])  # BGR -> RGB
    image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
    return image.copy()  # detach from the temporary numpy buffer
