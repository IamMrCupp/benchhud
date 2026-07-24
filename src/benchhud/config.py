"""Typed configuration model + TOML persistence.

The config tree mirrors ``config.example.toml``. It loads with the stdlib
``tomllib`` and writes with ``tomlkit``. Reads are lenient — unknown keys are
ignored and missing keys fall back to defaults — so a hand-edited or
older-version config never hard-fails the app.

The live config is app-managed (window geometry, dock layout), so writes go to a
platform-appropriate path (overridable with ``$BENCHHUD_CONFIG``), separate from
the documented example file in the repo.
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

APP_NAME = "benchhud"
ENV_VAR = "BENCHHUD_CONFIG"


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class ShellConfig:
    window_width: int = 1200
    window_height: int = 760
    # Opaque base64 of QMainWindow.saveState(); None until first save.
    layout: str | None = None
    # Which default-arrangement version produced `layout`. A saved layout from
    # an older default is ignored so a new default isn't silently overridden.
    layout_version: int = 0
    logging: LoggingConfig = field(default_factory=LoggingConfig)


@dataclass
class OutputConfig:
    enabled: bool = False
    # The v4l2loopback node benchhud writes the composite to; OBS reads it.
    device: str = "/dev/video10"
    width: int = 1280
    height: int = 720
    fps: int = 15


@dataclass
class ScopePanelConfig:
    enabled: bool = True
    device: str = "/dev/video4"


@dataclass
class ThermalPanelConfig:
    enabled: bool = False
    device: str = "/dev/video6"


@dataclass
class TelemetryPanelConfig:
    enabled: bool = False
    port: str = "/dev/ttyUSB0"
    baud: int = 115200


@dataclass
class PanelsConfig:
    scope: ScopePanelConfig = field(default_factory=ScopePanelConfig)
    thermal: ThermalPanelConfig = field(default_factory=ThermalPanelConfig)
    telemetry: TelemetryPanelConfig = field(default_factory=TelemetryPanelConfig)


@dataclass
class ToolConfig:
    name: str
    command: str


@dataclass
class OverlayConfig:
    """Overlay preferences. ``colormap`` is a name (e.g. INFERNO), not a cv2 int —
    the mapping lives in the overlay adapter so config stays cv2-free."""

    mode: str = "blend"  # blend | tint | marker
    alpha: float = 0.5
    threshold: float = 60.0
    tmin: float = 20.0
    tmax: float = 100.0
    colormap: str = "INFERNO"


@dataclass
class RegistrationConfig:
    """Persisted thermal→scope calibration transform."""

    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0


@dataclass
class FaultConfig:
    """Auto-logging of over-current faults to the selected repair job."""

    auto_log: bool = False
    current_threshold_a: float = 1.0


@dataclass
class Config:
    shell: ShellConfig = field(default_factory=ShellConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    panels: PanelsConfig = field(default_factory=PanelsConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    registration: RegistrationConfig = field(default_factory=RegistrationConfig)
    faults: FaultConfig = field(default_factory=FaultConfig)
    tools: list[ToolConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Config:
        """Build a Config from parsed TOML, filling defaults and ignoring extras."""
        cfg = cls()

        shell_d = data.get("shell", {})
        _apply(
            cfg.shell,
            shell_d,
            ("window_width", "window_height", "layout", "layout_version"),
        )
        _apply(cfg.shell.logging, shell_d.get("logging", {}), ("level",))

        _apply(
            cfg.output,
            data.get("output", {}),
            ("enabled", "device", "width", "height", "fps"),
        )

        panels_d = data.get("panels", {})
        _apply(cfg.panels.scope, panels_d.get("scope", {}), ("enabled", "device"))
        _apply(cfg.panels.thermal, panels_d.get("thermal", {}), ("enabled", "device"))
        _apply(
            cfg.panels.telemetry,
            panels_d.get("telemetry", {}),
            ("enabled", "port", "baud"),
        )

        _apply(
            cfg.overlay,
            data.get("overlay", {}),
            ("mode", "alpha", "threshold", "tmin", "tmax", "colormap"),
        )
        _apply(
            cfg.registration,
            data.get("registration", {}),
            ("scale", "offset_x", "offset_y", "rotation_deg"),
        )
        _apply(
            cfg.faults,
            data.get("faults", {}),
            ("auto_log", "current_threshold_a"),
        )

        cfg.tools = [
            ToolConfig(name=t["name"], command=t["command"])
            for t in data.get("tools", [])
            if isinstance(t, Mapping) and "name" in t and "command" in t
        ]
        return cfg

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict, dropping keys TOML can't represent (None)."""
        d = asdict(self)
        if d["shell"].get("layout") is None:
            d["shell"].pop("layout", None)
        return d


def _apply(obj: Any, data: Mapping[str, Any], keys: tuple[str, ...]) -> None:
    """Copy present ``keys`` from ``data`` onto dataclass instance ``obj``."""
    for key in keys:
        if key in data:
            setattr(obj, key, data[key])


def default_config_path() -> Path:
    """Where the live config lives: ``$BENCHHUD_CONFIG`` or a per-OS config dir."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME / "config.toml"


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """Load config from ``path`` (or the default). Missing file → defaults."""
    target = Path(path) if path is not None else default_config_path()
    if not target.exists():
        return Config()
    return Config.from_dict(tomllib.loads(target.read_text("utf-8")))


def save(config: Config, path: str | os.PathLike[str] | None = None) -> Path:
    """Persist ``config`` to ``path`` (or the default). Returns the path written."""
    target = Path(path) if path is not None else default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomlkit.dumps(config.to_dict()), "utf-8")
    return target


# Type alias for the "persist this config" callback the shell invokes on close.
PersistCallback = Callable[[Config], None]
