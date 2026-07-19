"""Tests for overlay/registration config persistence + adapters."""

from __future__ import annotations

from benchhud import config as cfg
from benchhud.overlay.composite import OverlayMode, OverlaySettings
from benchhud.overlay.config_adapter import (
    colormap_from_name,
    colormap_to_name,
    config_from_settings,
    config_from_transform,
    settings_from_config,
    transform_from_config,
)
from benchhud.overlay.transform import RegistrationTransform


def test_config_defaults() -> None:
    c = cfg.Config()
    assert c.overlay.mode == "blend"
    assert c.overlay.colormap == "INFERNO"
    assert c.registration.scale == 1.0


def test_config_round_trip_persists_overlay_and_registration(tmp_path) -> None:
    c = cfg.Config()
    c.overlay.mode = "tint"
    c.overlay.threshold = 75.0
    c.overlay.colormap = "TURBO"
    c.registration.scale = 1.8
    c.registration.offset_x = 12.0
    c.registration.rotation_deg = 5.0

    path = tmp_path / "config.toml"
    cfg.save(c, path)
    assert cfg.load(path) == c


def test_colormap_name_round_trip() -> None:
    for name in ("INFERNO", "TURBO", "JET", "HOT"):
        assert colormap_to_name(colormap_from_name(name)) == name


def test_unknown_colormap_falls_back() -> None:
    assert colormap_from_name("NONSENSE") == colormap_from_name("INFERNO")


def test_settings_from_config_maps_mode_and_colormap() -> None:
    oc = cfg.OverlayConfig(mode="marker", colormap="JET", alpha=0.3)
    s = settings_from_config(oc)
    assert s.mode is OverlayMode.MARKER
    assert s.alpha == 0.3
    assert s.colormap == colormap_from_name("JET")


def test_settings_from_config_unknown_mode_defaults_blend() -> None:
    s = settings_from_config(cfg.OverlayConfig(mode="bogus"))
    assert s.mode is OverlayMode.BLEND


def test_settings_config_round_trip() -> None:
    s = OverlaySettings(mode=OverlayMode.TINT, alpha=0.7, threshold=55.0)
    back = settings_from_config(config_from_settings(s))
    assert back.mode is s.mode
    assert back.alpha == s.alpha
    assert back.threshold == s.threshold


def test_transform_config_round_trip() -> None:
    t = RegistrationTransform(scale=1.5, offset_x=3.0, offset_y=-4.0, rotation_deg=10.0)
    back = transform_from_config(config_from_transform(t))
    assert back == t
