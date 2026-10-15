"""Compare the live scope with a known-good board's reference image (#256).

A missing cap or a lifted pin jumps out against a good board at the same
framing. The reference is scaled to cover the frame and centred, the same fit
as the thermal (#242), with its own nudge for small framing differences. Then
it's either faded over the live image or swapped in on a blink.

Pure array math; the caller supplies the clock for blink.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from benchhud.overlay.transform import RegistrationTransform


class CompareMode(Enum):
    OFF = "off"
    OVERLAY = "overlay"
    BLINK = "blink"


#: Blink rate: each image shows for this long.
BLINK_SECONDS = 0.5


@dataclass(frozen=True)
class ReferenceSettings:
    mode: CompareMode = CompareMode.OFF
    opacity: float = 0.5
    offset_x: float = 0.0
    offset_y: float = 0.0


def fit_reference(
    reference: np.ndarray, size: tuple[int, int], settings: ReferenceSettings
) -> np.ndarray:
    """The reference covering a ``size`` = (width, height) frame, centred and nudged."""
    transform = RegistrationTransform(offset_x=settings.offset_x, offset_y=settings.offset_y)
    return transform.warp(reference, size)


def compare(
    live: np.ndarray, reference: np.ndarray | None, settings: ReferenceSettings, now: float
) -> np.ndarray:
    """The live frame with the reference applied, per the mode."""
    if reference is None or settings.mode is CompareMode.OFF:
        return live
    fitted = fit_reference(reference, (live.shape[1], live.shape[0]), settings)
    if fitted.shape != live.shape:  # a grey reference on a colour scope, say
        fitted = cv2.cvtColor(fitted, cv2.COLOR_GRAY2BGR) if fitted.ndim == 2 else fitted
    if settings.mode is CompareMode.BLINK:
        return fitted if int(now / BLINK_SECONDS) % 2 else live
    alpha = float(np.clip(settings.opacity, 0.0, 1.0))
    return cv2.addWeighted(live, 1.0 - alpha, fitted, alpha, 0.0)
