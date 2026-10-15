"""Two-point thermal calibration with a hot tip (#243).

Touch a hot iron tip to the board, click the tip in the scope view, and the
thermal camera's hottest pixel is the same spot. Do it at two places and the
scale, rotation and offset of the thermal→scope transform are determined
exactly: two point pairs fix a similarity transform.

Pure: points in, a :class:`RegistrationTransform` out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from benchhud.overlay.transform import RegistrationTransform
from benchhud.thermal.analyze import hottest

#: How far above the frame's median the peak must be to count as the tip.
MIN_CONTRAST_C = 5.0
#: How far apart the two thermal points must be, as a fraction of the thermal
#: frame's shorter side. Close points make the scale and rotation shaky.
MIN_SPREAD = 0.2


class CalibrationError(ValueError):
    """A capture or a solve that can't be trusted; the message says why."""


@dataclass(frozen=True)
class Pair:
    thermal: tuple[float, float]  # the hot pixel, in thermal pixels
    scope: tuple[float, float]  # where it was clicked, in scope pixels
    temperature: float


def find_tip(temps: np.ndarray) -> tuple[tuple[float, float], float]:
    """The hot tip in a thermal frame: its pixel and °C.

    Refuses a frame with no clear peak (the iron isn't touching, or it's cold).
    """
    spot = hottest(temps)
    ambient = float(np.median(temps))
    if spot.value - ambient < MIN_CONTRAST_C:
        raise CalibrationError(
            f"No clear hot spot: the peak is {spot.value:.1f} °C against "
            f"{ambient:.1f} °C. Touch the iron tip to the board and try again."
        )
    return (float(spot.x), float(spot.y)), spot.value


def solve(
    first: Pair, second: Pair, src_size: tuple[int, int], dst_size: tuple[int, int]
) -> RegistrationTransform:
    """The transform that maps both thermal points onto their scope points."""
    ax = second.thermal[0] - first.thermal[0]
    ay = second.thermal[1] - first.thermal[1]
    bx = second.scope[0] - first.scope[0]
    by = second.scope[1] - first.scope[1]
    spread = math.hypot(ax, ay)
    if spread < MIN_SPREAD * min(src_size):
        raise CalibrationError(
            "The two spots are too close together in the thermal image. Pick spots "
            "further apart, near opposite corners of what the scope sees."
        )
    if math.hypot(bx, by) < 1:
        raise CalibrationError("Both clicks landed on the same place in the scope view.")

    # The rotation-and-scale part of OpenCV's matrix is [[alpha, beta], [-beta, alpha]], y
    # pointing down. Solve it from the vector between the two points.
    alpha = (ax * bx + ay * by) / (spread * spread)
    beta = (ay * bx - ax * by) / (spread * spread)
    total_scale = math.hypot(alpha, beta)
    rotation = math.degrees(math.atan2(beta, alpha))

    cx, cy = src_size[0] / 2.0, src_size[1] / 2.0
    k = RegistrationTransform.cover(src_size, dst_size, rotation)
    # Where the first thermal point lands with no offset, then the offset that
    # moves it onto its click.
    rx, ry = first.thermal[0] - cx, first.thermal[1] - cy
    landed_x = dst_size[0] / 2.0 + alpha * rx + beta * ry
    landed_y = dst_size[1] / 2.0 - beta * rx + alpha * ry
    return RegistrationTransform(
        scale=total_scale / k,
        offset_x=first.scope[0] - landed_x,
        offset_y=first.scope[1] - landed_y,
        rotation_deg=rotation,
        fit=True,
    )
