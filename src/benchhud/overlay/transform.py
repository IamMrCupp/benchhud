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

import math
from dataclasses import dataclass, replace

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

    ``rotated_fit`` (#259): the cover fit uses the *rotated* thermal's bounding
    box, so a portrait thermal turned 90° fills a landscape scope at scale 1.0.

    Older placements are kept so a saved transform keeps working until
    :meth:`migrated` converts it. ``fit=False`` is the original: native size,
    scale and rotation about the thermal frame's own centre, absolute offsets,
    which left the thermal in the scope frame's top-left corner.
    ``rotated_fit=False`` is #242's, which fitted the unrotated thermal.
    """

    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0
    fit: bool = True
    rotated_fit: bool = True

    @staticmethod
    def cover(
        src_size: tuple[int, int], dst_size: tuple[int, int], rotation_deg: float = 0.0
    ) -> float:
        """The scale at which ``src``, turned by ``rotation_deg``, covers ``dst``."""
        theta = math.radians(rotation_deg)
        cos, sin = abs(math.cos(theta)), abs(math.sin(theta))
        width = src_size[0] * cos + src_size[1] * sin
        height = src_size[0] * sin + src_size[1] * cos
        return max(dst_size[0] / width, dst_size[1] / height)

    def _fit_scale(self, src_size: tuple[int, int], dst_size: tuple[int, int]) -> float:
        rotation = self.rotation_deg if self.rotated_fit else 0.0
        return self.cover(src_size, dst_size, rotation)

    @property
    def current(self) -> bool:
        """Whether this is the current placement (no conversion needed)."""
        return self.fit and self.rotated_fit

    def turned(self, quarter_turns: int) -> RegistrationTransform:
        """This transform turned by 90° steps (positive = counter-clockwise)."""
        rotation = (self.rotation_deg + 90.0 * quarter_turns + 180.0) % 360.0 - 180.0
        return replace(self, rotation_deg=rotation)

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
        k = self._fit_scale(src_size, dst_size)
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
        """The current-placement transform that puts every pixel where this one does."""
        if self.current:
            return self
        k = self.cover(src_size, dst_size, self.rotation_deg)
        if not self.fit:
            # The original: native size, top-left (#242).
            return RegistrationTransform(
                scale=self.scale / k,
                offset_x=self.offset_x + src_size[0] / 2.0 - dst_size[0] / 2.0,
                offset_y=self.offset_y + src_size[1] / 2.0 - dst_size[1] / 2.0,
                rotation_deg=self.rotation_deg,
            )
        # #242's fit ignored the rotation; same centre, so only the scale changes.
        k_old = self.cover(src_size, dst_size, 0.0)
        return replace(self, scale=self.scale * k_old / k, rotated_fit=True)

    def warp(self, src: np.ndarray, dst_size: tuple[int, int]) -> np.ndarray:
        """Warp ``src`` into a ``dst_size`` = ``(width, height)`` frame."""
        src_size = (src.shape[1], src.shape[0])
        matrix = self.affine_matrix(src_size, dst_size)
        return cv2.warpAffine(src, matrix, dst_size)
