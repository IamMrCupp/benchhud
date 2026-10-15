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
    # The resolution to ask the camera for (#247); 0 x 0 takes its default.
    width: int = 0
    height: int = 0


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
    # A command-line tool: it runs in a terminal (the Terminal panel, or a
    # system terminal window) instead of being launched as a window (#225).
    terminal: bool = False


@dataclass
class OverlayConfig:
    """Overlay preferences. ``colormap`` is a name (e.g. INFERNO), not a cv2 int —
    the mapping lives in the overlay adapter so config stays cv2-free."""

    mode: str = "blend"  # blend | outline | tint | marker
    alpha: float = 0.5
    threshold: float = 60.0
    # The colour range: auto follows the board (ambient to the hottest point);
    # off uses tmin / tmax.
    auto_range: bool = True
    tmin: float = 20.0
    tmax: float = 100.0
    colormap: str = "INFERNO"
    scale_bar: bool = True  # a small colour scale with the range, top left


@dataclass
class RegistrationConfig:
    """Persisted thermal→scope calibration transform."""

    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0
    # 2: the thermal starts fitted to the scope frame and centred (#242).
    # 1: the original native-size, top-left placement; converted on first use.
    version: int = 2


@dataclass
class FaultConfig:
    """Auto-logging of over-current faults to the selected repair job."""

    auto_log: bool = False
    current_threshold_a: float = 1.0


@dataclass
class TelemetryOverlayConfig:
    """Burn live power/DMM readings into the composited (streamed) frame.

    Off by default — benchhud is a standalone bench app, so the meter dock
    already shows readings locally; this is the on-stream copy. PII-free by
    nature (electrical readings only)."""

    enabled: bool = False
    # Where the readout sits on the frame.
    corner: str = "bottom-left"  # bottom-left | bottom-right | top-left | top-right
    show_psu: bool = True  # VOLTAGE / CURRENT / POWER / MAX CURRENT
    show_dmm: bool = True  # the DMM header line
    # Size multiplier on top of the automatic frame-relative scaling.
    scale: float = 1.0
    # Overlay alpha — below 1.0 lets the board show through the readout.
    opacity: float = 1.0


@dataclass
class GraphOverlayConfig:
    """Burn the live V/A/W power trend into the composited (streamed) frame.

    Off by default, like the meter overlay. PII-free (electrical readings only).
    In the same corner as the meter readout it stacks beside it, never over it."""

    enabled: bool = False
    corner: str = "bottom-right"  # bottom-left | bottom-right | top-left | top-right
    window_seconds: float = 60.0  # how much history the trend shows
    scale: float = 1.0  # size multiplier on top of the automatic frame scaling
    opacity: float = 0.9  # below 1.0 lets the board show through


@dataclass
class BrowserSourcesConfig:
    """Serve data panels to OBS as Browser sources on loopback (#180).

    Off by default: nothing listens on a port unless asked to. The server only
    ever binds ``127.0.0.1``, so there's no host to configure."""

    enabled: bool = False
    port: int = 8780


@dataclass
class TerminalConfig:
    """The in-app terminal and its read-only stream view (#194).

    Off by default, and sharing is a second, separate switch: a live shell
    shows whatever scrolls past (tokens, keys, customer paths), so it only goes
    on air when asked, and the panel's Pause hides it mid-stream. It runs on the
    browser-source server's port, bound to ``127.0.0.1``."""

    enabled: bool = False
    shell: str = ""  # empty = your login shell
    share: bool = False  # serve the read-only view to OBS
    # The stream view's link token. Generated on first use and kept, so an OBS
    # source URL survives a relaunch. Regenerate it from Preferences.
    share_token: str = ""
    # What the terminal is attached to (#174): "shell", "owon" (the SCPI query
    # console, through the instrument session), or a serial port's device path.
    # The OWON's own port always means "owon": it's never opened a second time.
    target: str = "shell"
    # For a raw serial port: speed, what Enter sends (cr | lf | crlf), and
    # whether to echo typing locally (many devices don't echo).
    baud: int = 115200
    line_ending: str = "crlf"
    local_echo: bool = False


@dataclass
class IntakeConfig:
    """Connection to a benchhud-intake ticket API (empty base URL → disabled)."""

    base_url: str = ""
    token: str = ""


@dataclass
class InventoryConfig:
    """Connection to a bench-parts inventory API (empty base URL → disabled)."""

    base_url: str = ""
    token: str = ""


@dataclass
class InstrumentsConfig:
    """Control policy for the bench instrument (#164).

    Policy only — the serial device itself stays on ``panels.telemetry``, which
    already owns it. Two places naming the same port is how they drift apart.

    ``control`` is off by default and is the switch that lets the HUD change
    instrument state at all. This is a supply wired to other people's boards:
    read-only telemetry is the posture you get unless you ask for more.
    """

    control: bool = False
    # Hard ceilings the driver refuses to exceed. Conservative on purpose —
    # enough for logic rails and phone-board work.
    max_voltage: float = 5.5
    max_current: float = 1.0
    # Seconds between telemetry polls on the session's owner thread.
    poll_interval: float = 0.2


@dataclass
class BoardsConfig:
    """Local board-view library (empty directory → no library configured)."""

    library_dir: str = ""
    # Board-view formats a viewer reads.
    extensions: list[str] = field(default_factory=lambda: ["obv", "brd", "bdv"])
    # Schematic formats stored alongside the board (same stem). A viewer like
    # FlexBV auto-loads the matching-name PDF and cross-probes against it.
    schematic_extensions: list[str] = field(default_factory=lambda: ["pdf"])
    # Which board viewer to open a board in: auto | openboardview | flexbv.
    viewer: str = "auto"


@dataclass
class FlashConfig:
    """The flashrom panel (#227): which flashrom, and the last programmer used."""

    command: str = "flashrom"  # on PATH, or an absolute path
    programmer: str = "ch341a_spi"  # flashrom's -p value


@dataclass
class Config:
    shell: ShellConfig = field(default_factory=ShellConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    panels: PanelsConfig = field(default_factory=PanelsConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    registration: RegistrationConfig = field(default_factory=RegistrationConfig)
    faults: FaultConfig = field(default_factory=FaultConfig)
    telemetry_overlay: TelemetryOverlayConfig = field(default_factory=TelemetryOverlayConfig)
    graph_overlay: GraphOverlayConfig = field(default_factory=GraphOverlayConfig)
    browser_sources: BrowserSourcesConfig = field(default_factory=BrowserSourcesConfig)
    terminal: TerminalConfig = field(default_factory=TerminalConfig)
    intake: IntakeConfig = field(default_factory=IntakeConfig)
    inventory: InventoryConfig = field(default_factory=InventoryConfig)
    instruments: InstrumentsConfig = field(default_factory=InstrumentsConfig)
    boards: BoardsConfig = field(default_factory=BoardsConfig)
    flash: FlashConfig = field(default_factory=FlashConfig)
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
        _apply(
            cfg.panels.scope, panels_d.get("scope", {}), ("enabled", "device", "width", "height")
        )
        _apply(cfg.panels.thermal, panels_d.get("thermal", {}), ("enabled", "device"))
        _apply(
            cfg.panels.telemetry,
            panels_d.get("telemetry", {}),
            ("enabled", "port", "baud"),
        )

        _apply(
            cfg.overlay,
            data.get("overlay", {}),
            (
                "mode",
                "alpha",
                "threshold",
                "auto_range",
                "tmin",
                "tmax",
                "colormap",
                "scale_bar",
            ),
        )
        registration = data.get("registration", {})
        _apply(
            cfg.registration,
            registration,
            ("scale", "offset_x", "offset_y", "rotation_deg", "version"),
        )
        if registration and "version" not in registration:
            cfg.registration.version = 1  # saved before #242: the old placement
        _apply(
            cfg.faults,
            data.get("faults", {}),
            ("auto_log", "current_threshold_a"),
        )
        _apply(
            cfg.telemetry_overlay,
            data.get("telemetry_overlay", {}),
            ("enabled", "corner", "show_psu", "show_dmm", "scale", "opacity"),
        )
        _apply(
            cfg.graph_overlay,
            data.get("graph_overlay", {}),
            ("enabled", "corner", "window_seconds", "scale", "opacity"),
        )
        _apply(cfg.browser_sources, data.get("browser_sources", {}), ("enabled", "port"))
        _apply(
            cfg.terminal,
            data.get("terminal", {}),
            (
                "enabled",
                "shell",
                "share",
                "share_token",
                "target",
                "baud",
                "line_ending",
                "local_echo",
            ),
        )
        _apply(
            cfg.intake,
            data.get("intake", {}),
            ("base_url", "token"),
        )
        _apply(
            cfg.inventory,
            data.get("inventory", {}),
            ("base_url", "token"),
        )

        _apply(
            cfg.instruments,
            data.get("instruments", {}),
            ("control", "max_voltage", "max_current", "poll_interval"),
        )
        _apply(
            cfg.boards,
            data.get("boards", {}),
            ("library_dir", "extensions", "schematic_extensions", "viewer"),
        )
        _apply(cfg.flash, data.get("flash", {}), ("command", "programmer"))

        cfg.tools = [
            ToolConfig(
                name=t["name"], command=t["command"], terminal=bool(t.get("terminal", False))
            )
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
