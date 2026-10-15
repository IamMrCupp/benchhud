"""Map persisted config ↔ runtime overlay/registration objects.

Kept out of ``config.py`` so that module stays free of the cv2 dependency: the
colormap is stored as a name in config and resolved to a cv2 constant here.
Unknown names / modes fall back to sensible defaults rather than raising, so a
hand-edited config never hard-fails.
"""

from __future__ import annotations

import cv2

from benchhud.config import OverlayConfig, RegistrationConfig
from benchhud.overlay.composite import OverlayMode, OverlaySettings
from benchhud.overlay.transform import RegistrationTransform

_COLORMAPS: dict[str, int] = {
    "INFERNO": cv2.COLORMAP_INFERNO,
    "MAGMA": cv2.COLORMAP_MAGMA,
    "PLASMA": cv2.COLORMAP_PLASMA,
    "VIRIDIS": cv2.COLORMAP_VIRIDIS,
    "TURBO": cv2.COLORMAP_TURBO,
    "JET": cv2.COLORMAP_JET,
    "HOT": cv2.COLORMAP_HOT,
}
_DEFAULT_COLORMAP_NAME = "INFERNO"


def colormap_names() -> list[str]:
    """Available colormap names, in display order."""
    return list(_COLORMAPS)


def colormap_from_name(name: str) -> int:
    return _COLORMAPS.get(name.upper(), _COLORMAPS[_DEFAULT_COLORMAP_NAME])


def colormap_to_name(value: int) -> str:
    for name, constant in _COLORMAPS.items():
        if constant == value:
            return name
    return _DEFAULT_COLORMAP_NAME


def settings_from_config(cfg: OverlayConfig) -> OverlaySettings:
    try:
        mode = OverlayMode(cfg.mode)
    except ValueError:
        mode = OverlayMode.BLEND
    return OverlaySettings(
        mode=mode,
        alpha=cfg.alpha,
        threshold=cfg.threshold,
        tmin=cfg.tmin,
        tmax=cfg.tmax,
        colormap=colormap_from_name(cfg.colormap),
        auto_range=cfg.auto_range,
        scale_bar=cfg.scale_bar,
    )


def config_from_settings(settings: OverlaySettings) -> OverlayConfig:
    return OverlayConfig(
        mode=settings.mode.value,
        alpha=settings.alpha,
        threshold=settings.threshold,
        tmin=settings.tmin,
        tmax=settings.tmax,
        colormap=colormap_to_name(settings.colormap),
        auto_range=settings.auto_range,
        scale_bar=settings.scale_bar,
    )


def transform_from_config(cfg: RegistrationConfig) -> RegistrationTransform:
    return RegistrationTransform(
        scale=cfg.scale,
        offset_x=cfg.offset_x,
        offset_y=cfg.offset_y,
        rotation_deg=cfg.rotation_deg,
        fit=cfg.version >= 2,
    )


def config_from_transform(transform: RegistrationTransform) -> RegistrationConfig:
    return RegistrationConfig(
        scale=transform.scale,
        offset_x=transform.offset_x,
        offset_y=transform.offset_y,
        rotation_deg=transform.rotation_deg,
        version=2 if transform.fit else 1,
    )
