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

    With ``fit`` (the default, #242), the thermal frame starts out scaled to
    *cover* the scope frame and centred on it. ``scale`` multiplies that fit
    (1.0 = same field of view; above 1 zooms the thermal in to match a narrower
    scope view), ``rotation_deg`` turns about the centre, and the offsets nudge
    it in scope pixels.

    ``fit=False`` is the original placement, kept so a saved transform keeps
    working until :meth:`migrated` converts it: native size, with scale and
    rotation about the thermal frame's own centre and absolute offsets, which
    left the thermal in the scope frame's top-left corner.
    """

    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0
    fit: bool = True

    @staticmethod
    def cover(src_size: tuple[int, int], dst_size: tuple[int, int]) -> float:
        """The scale at which ``src`` covers ``dst`` (no letterbox)."""
        return max(dst_size[0] / src_size[0], dst_size[1] / src_size[1])

    def affine_matrix(
        self, src_size: tuple[int, int], dst_size: tuple[int, int] | None = None
    ) -> np.ndarray:
        """The 2x3 affine matrix for a ``(width, height)`` source into ``dst_size``."""
        width, height = src_size
        center = (width / 2.0, height / 2.0)
        if not self.fit or dst_size is None:
            matrix = cv2.getRotationMatrix2D(center, self.rotation_deg, self.scale)
            matrix[0, 2] += self.offset_x
            matrix[1, 2] += self.offset_y
            return matrix
        k = self.cover(src_size, dst_size)
        matrix = cv2.getRotationMatrix2D(center, self.rotation_deg, self.scale * k)
        # Move the thermal centre onto the scope centre, then nudge.
        matrix[0, 2] += dst_size[0] / 2.0 - center[0] + self.offset_x
        matrix[1, 2] += dst_size[1] / 2.0 - center[1] + self.offset_y
        return matrix

    def map_point(
        self, x: float, y: float, src_size: tuple[int, int], dst_size: tuple[int, int]
    ) -> tuple[float, float]:
        """Where thermal pixel ``(x, y)`` lands in the scope frame."""
        m = self.affine_matrix(src_size, dst_size)
        return (
            float(m[0, 0] * x + m[0, 1] * y + m[0, 2]),
            float(m[1, 0] * x + m[1, 1] * y + m[1, 2]),
        )

    def migrated(
        self, src_size: tuple[int, int], dst_size: tuple[int, int]
    ) -> RegistrationTransform:
        """The ``fit`` transform that puts every pixel where this one does."""
        if self.fit:
            return self
        k = self.cover(src_size, dst_size)
        return RegistrationTransform(
            scale=self.scale / k,
            offset_x=self.offset_x + src_size[0] / 2.0 - dst_size[0] / 2.0,
            offset_y=self.offset_y + src_size[1] / 2.0 - dst_size[1] / 2.0,
            rotation_deg=self.rotation_deg,
            fit=True,
        )

    def warp(self, src: np.ndarray, dst_size: tuple[int, int]) -> np.ndarray:
        """Warp ``src`` into a ``dst_size`` = ``(width, height)`` frame."""
        src_size = (src.shape[1], src.shape[0])
        matrix = self.affine_matrix(src_size, dst_size)
        return cv2.warpAffine(src, matrix, dst_size)
