"""Registration transform: map thermal-camera space into scope-image space.

The scope and thermal cameras have different fields of view, resolution, and
physical mounting, so the thermal frame must be scaled / offset / rotated to line
up with the scope image (plan §3, the hard part). This module is just the
*mechanism* — a manual/calibrated affine transform applied with
``cv2.warpAffine``. The transform *values* come from manual sliders (Phase 4) or
a saved calibration (Phase 6); tuning them needs the bench, but the warp math is
hardware-free and tested here.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class RegistrationTransform:
    """Scale + translate + rotate mapping thermal space → scope space.

    Rotation and scale are about the source image's center; ``offset_x`` /
    ``offset_y`` then translate in destination pixels.
    """

    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0

    def affine_matrix(self, src_size: tuple[int, int]) -> np.ndarray:
        """Return the 2x3 affine matrix for a source of ``(width, height)``."""
        width, height = src_size
        center = (width / 2.0, height / 2.0)
        matrix = cv2.getRotationMatrix2D(center, self.rotation_deg, self.scale)
        matrix[0, 2] += self.offset_x
        matrix[1, 2] += self.offset_y
        return matrix

    def warp(self, src: np.ndarray, dst_size: tuple[int, int]) -> np.ndarray:
        """Warp ``src`` into a ``dst_size`` = ``(width, height)`` frame."""
        src_size = (src.shape[1], src.shape[0])
        matrix = self.affine_matrix(src_size)
        return cv2.warpAffine(src, matrix, dst_size)
