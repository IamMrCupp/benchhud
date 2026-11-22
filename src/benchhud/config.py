"""Typed configuration model + TOML persistence.

The config tree mirrors ``config.example.toml``. It loads with the stdlib
``tomllib`` and writes with ``tomlkit``. Reads are lenient — unknown keys are
ignored, and a missing key, a value of the wrong type or a section of the
wrong shape falls back to its default — so a hand-edited or older-version
config never hard-fails the app. A value that's dropped is logged, with its
section and key, because the next save writes the default over it.

The one thing leniency can't cover is a file that isn't TOML at all, or can't
be read. :func:`load` raises :class:`ConfigError` for that, naming the file
and the line, and the caller decides: the app comes up on defaults and saves
nothing over the file; a command that would rewrite it stops instead.

Writes are checked before the file is touched: what's about to be written has
to load back, so a save can't leave a config the next start chokes on. Then
it's written beside the file and moved into place in one step, so a write that
fails halfway — a full disk — leaves the old file whole.

The live config is app-managed (window geometry, dock layout), so writes go to a
platform-appropriate path (overridable with ``$BENCHHUD_CONFIG``), separate from
the documented example file in the repo.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import re
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

log = logging.getLogger(__name__)

APP_NAME = "benchhud"
ENV_VAR = "BENCHHUD_CONFIG"


class ConfigError(ValueError):
    """The config file is there but can't be used, or can't be written.

    ``path`` is the file and ``reason`` is what's wrong with it — for a file
    that isn't valid TOML, the parser's own words, line and column included.
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


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
    # "system", "light", "dark", or "theme:<file>" for a theme file in the
    # themes folder (see benchhud.themes). Dark until someone picks otherwise.
    appearance: str = "dark"
    logging: LoggingConfig = field(default_factory=LoggingConfig)


@dataclass
class OutputConfig:
    enabled: bool = False
    # The v4l2loopback node benchhud writes the composite to; OBS reads it.
    device: str = "/dev/video10"
    width: int = 1280
    height: int = 720
    # Paces the whole frame loop while the output is on (#359), not just the sink.
    fps: int = 15
    # Extra feeds so OBS can lay the cameras out itself (#344). Empty = off.
    # Linux only: each is its own v4l2loopback node.
    scope_device: str = ""  # the scope alone, at width x height
    thermal_device: str = ""  # the thermal alone, colourised, its own shape, same height


@dataclass
class ScopePanelConfig:
    enabled: bool = True
    device: str = "/dev/video4"
    # The resolution to ask the camera for (#247); 0 x 0 takes its default.
    width: int = 0
    height: int = 0
    # Decode the camera's MJPEG small for the live view (#349): less CPU, a softer
    # live picture. Off decodes every frame at full size. Snapshots are full size
    # either way (#381).
    reduced_live: bool = True


@dataclass
class ThermalPanelConfig:
    enabled: bool = False
    device: str = "/dev/video6"
    # The camera's hub port as "hub:port" (e.g. "1-4:4", from `uhubctl`). When
    # set, a camera that's been gone 30 s gets its port reset once (#276). Needs
    # uhubctl and passwordless sudo for it. Empty: never.
    reset_port: str = ""


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
    # The OBS scene this tool's window is in (#455). When set, and [obs] allows
    # it, starting the tool shows that scene and its exit goes back.
    scene: str = ""


@dataclass
class ObsConfig:
    """Choosing OBS's live scene from the HUD (#455), over obs-websocket.

    OBS still owns the pictures; this only picks which scene is on. Off by
    default, and switching on its own when a tool starts is a second opt-in:
    what's on air shouldn't change by surprise.
    """

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 4455
    # Empty: read it from the OBS on this machine (its own config file).
    password: str = ""
    auto_switch: bool = False


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
    # 4: fitted to the scope frame, centred, the fit following the nearest
    # quarter turn (#318). 3: the fit following the exact rotation (#259).
    # 2: fitted ignoring the rotation (#242). 1: native size, top left.
    # Older versions are converted on first use, with no visible jump.
    version: int = 4


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
class ReconConfig:
    """Connection to a bench-recon engagement API (empty base URL → disabled)."""

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


#: Whether a chip source is on air when its entry doesn't say. The one place
#: that default lives: the config loader, a new row in Preferences and the
#: dataclass below all read it from here.
CHIP_SOURCE_ON_AIR = True


@dataclass
class ChipSourceConfig:
    """One place chip data is read from (a ``[[chips.sources]]`` entry)."""

    name: str
    # What kind of source it is. "dir" — a chip-data checkout, a build of one,
    # or a private overlay on disk — is the only kind there is so far.
    kind: str = "dir"
    path: str = ""
    enabled: bool = True
    # Whether the Chip panel — stream output — may show what this source says.
    # Off air, Find chip still uses it; the card and the inventory row don't.
    on_air: bool = CHIP_SOURCE_ON_AIR


@dataclass
class ChipsConfig:
    """The chip reference (no sources → no chip data, and nothing changes)."""

    # Where datasheets are kept. Read and kept; nothing uses it yet.
    datasheet_dir: str = ""
    # In order of precedence: the first source wins where two disagree.
    sources: list[ChipSourceConfig] = field(default_factory=list)


@dataclass
class MiniproConfig:
    """The T48 panel (#253): which minipro, and the last chip used."""

    command: str = "minipro"
    last_chip: str = ""  # e.g. W25Q64BV@SOIC8; the panel looks it up on start


@dataclass
class NowPlayingConfig:
    """The now-playing banner, an OBS Browser source (#251). Linux: it reads the
    desktop's media players (MPRIS), so Chrome playing SoundCloud just works."""

    enabled: bool = True  # served with the other browser sources
    hide_when_paused: bool = True
    label: str = "Now playing"


@dataclass
class PerformanceConfig:
    """Knobs that trade CPU for speed."""

    # Threads OpenCV may spread a resize or a blend over. Its own default is one
    # per core, which on the bench cost a core more than 2 for the same output
    # frame rate (#415). 0 leaves OpenCV's default alone.
    opencv_threads: int = 2


@dataclass
class FlashConfig:
    """The flashrom panel (#227): which flashrom, and the last programmer used."""

    command: str = "flashrom"  # on PATH, or an absolute path
    programmer: str = "ch341a_spi"  # flashrom's -p value


#: The axes a saved camera shot can hold (see benchhud.devices.camera_controls).
CAMERA_AXES = ("pan", "tilt", "zoom")


@dataclass
class CameraConfig:
    """A stream camera benchhud can aim (#330): its pan/tilt/zoom and saved shots.

    benchhud only moves the camera; the picture stays with OBS. ``shots`` maps a
    shot's name (Face, Desk, Reflow) to the raw control values to go back to.
    """

    name: str
    device: str  # its /dev/v4l/by-id link, so a USB drop doesn't lose it
    shots: dict[str, dict[str, int]] = field(default_factory=dict)


def _camera_from_dict(data: Any) -> CameraConfig | None:
    if not isinstance(data, Mapping):
        return None
    name, device = data.get("name"), data.get("device")
    if not (isinstance(name, str) and name and isinstance(device, str) and device):
        return None
    shots: dict[str, dict[str, int]] = {}
    raw_shots = data.get("shots", {})
    if isinstance(raw_shots, Mapping):
        for shot, values in raw_shots.items():
            if not isinstance(values, Mapping):
                continue
            kept = {
                axis: int(values[axis])
                for axis in CAMERA_AXES
                if isinstance(values.get(axis), int) and not isinstance(values[axis], bool)
            }
            if kept:
                shots[str(shot)] = kept
    return CameraConfig(name=name, device=device, shots=shots)


def _chip_source_from_dict(data: Any) -> ChipSourceConfig | None:
    """One ``[[chips.sources]]`` entry, or ``None`` when it can't be one.

    An entry needs a name — it's what a value's provenance is recorded under.
    A ``kind`` this benchhud doesn't know is kept as written, so saving the
    config doesn't throw away an entry a newer version would read.
    """
    if not isinstance(data, Mapping):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    kind = data.get("kind", "dir")
    path = data.get("path", "")
    return ChipSourceConfig(
        name=name.strip(),
        kind=kind.strip().lower() if isinstance(kind, str) and kind.strip() else "dir",
        path=path.strip() if isinstance(path, str) else "",
        enabled=data.get("enabled", True) is not False,
        on_air=_chip_source_on_air(name.strip(), data),
    )


def _meant_as_on_air(key: Any) -> bool:
    """Whether a key reads as a go at ``on_air`` that isn't it.

    ``on-air``, ``onair``, ``On_Air``, ``off_air``: the switch, misspelt. TOML
    takes any of them without complaint and they'd be ignored, which is the
    one outcome that mustn't be silent here.
    """
    return key != "on_air" and _air_key(key) in ("onair", "offair", "air")


def _air_key(key: Any) -> str:
    """A key as it's compared for that: letters and digits only, case ignored."""
    return "".join(c for c in key.casefold() if c.isalnum()) if isinstance(key, str) else ""


def _chip_source_on_air(name: str, data: Mapping[str, Any]) -> bool:
    """Whether a ``[[chips.sources]]`` entry is on air.

    No ``on_air`` key is the default. A key that's there and isn't a plain
    true or false keeps the source **off** air, and says so in the log:
    somebody wrote the key to hold a source back, and a typo mustn't be what
    puts it on the stream. The same goes for a key that's the switch misspelt
    (``on-air``, ``onair``, ``off_air``) — whatever it's set to.
    """
    misspelt = sorted(key for key in data if _meant_as_on_air(key))
    if misspelt:
        log.warning(
            "config: chip source %r has %s, which isn't a key — the switch is on_air;"
            " keeping it off the stream card",
            name,
            ", ".join(misspelt),
        )
        return False
    if "on_air" not in data:
        return CHIP_SOURCE_ON_AIR
    switch = _switch(data["on_air"])
    if switch is None:
        log.warning(
            "config: chip source %r has an on_air that isn't true or false;"
            " keeping it off the stream card",
            name,
        )
        return False
    return switch


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
    recon: ReconConfig = field(default_factory=ReconConfig)
    instruments: InstrumentsConfig = field(default_factory=InstrumentsConfig)
    boards: BoardsConfig = field(default_factory=BoardsConfig)
    chips: ChipsConfig = field(default_factory=ChipsConfig)
    flash: FlashConfig = field(default_factory=FlashConfig)
    minipro: MiniproConfig = field(default_factory=MiniproConfig)
    now_playing: NowPlayingConfig = field(default_factory=NowPlayingConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    obs: ObsConfig = field(default_factory=ObsConfig)
    tools: list[ToolConfig] = field(default_factory=list)
    cameras: list[CameraConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Config:
        """Build a Config from parsed TOML, filling defaults and ignoring extras.

        Nothing in ``data`` makes this raise: a value of the wrong type keeps
        its default, and a section that isn't a table counts as not there.
        """
        cfg = cls()
        if not isinstance(data, Mapping):
            return cfg

        shell_d = _table(data, "shell")
        _apply(
            "shell",
            cfg.shell,
            shell_d,
            ("window_width", "window_height", "layout", "layout_version", "appearance"),
        )
        _apply("shell.logging", cfg.shell.logging, _table(shell_d, "logging"), ("level",))

        _apply(
            "output",
            cfg.output,
            _table(data, "output"),
            ("enabled", "device", "width", "height", "fps", "scope_device", "thermal_device"),
        )

        panels_d = _table(data, "panels")
        _apply(
            "panels.scope",
            cfg.panels.scope,
            _table(panels_d, "scope"),
            ("enabled", "device", "width", "height", "reduced_live"),
        )
        _apply(
            "panels.thermal",
            cfg.panels.thermal,
            _table(panels_d, "thermal"),
            ("enabled", "device", "reset_port"),
        )
        _apply(
            "panels.telemetry",
            cfg.panels.telemetry,
            _table(panels_d, "telemetry"),
            ("enabled", "port", "baud"),
        )

        _apply(
            "overlay",
            cfg.overlay,
            _table(data, "overlay"),
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
        registration = _table(data, "registration")
        _apply(
            "registration",
            cfg.registration,
            registration,
            ("scale", "offset_x", "offset_y", "rotation_deg", "version"),
        )
        if registration and "version" not in registration:
            cfg.registration.version = 1  # saved before #242: the old placement
        _apply(
            "faults",
            cfg.faults,
            _table(data, "faults"),
            ("auto_log", "current_threshold_a"),
        )
        _apply(
            "telemetry_overlay",
            cfg.telemetry_overlay,
            _table(data, "telemetry_overlay"),
            ("enabled", "corner", "show_psu", "show_dmm", "scale", "opacity"),
        )
        _apply(
            "graph_overlay",
            cfg.graph_overlay,
            _table(data, "graph_overlay"),
            ("enabled", "corner", "window_seconds", "scale", "opacity"),
        )
        _apply(
            "browser_sources",
            cfg.browser_sources,
            _table(data, "browser_sources"),
            ("enabled", "port"),
        )
        _apply(
            "terminal",
            cfg.terminal,
            _table(data, "terminal"),
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
            "intake",
            cfg.intake,
            _table(data, "intake"),
            ("base_url", "token"),
        )
        _apply(
            "inventory",
            cfg.inventory,
            _table(data, "inventory"),
            ("base_url", "token"),
        )
        _apply(
            "recon",
            cfg.recon,
            _table(data, "recon"),
            ("base_url", "token"),
        )

        _apply(
            "instruments",
            cfg.instruments,
            _table(data, "instruments"),
            ("control", "max_voltage", "max_current", "poll_interval"),
        )
        _apply(
            "boards",
            cfg.boards,
            _table(data, "boards"),
            ("library_dir", "extensions", "schematic_extensions", "viewer"),
        )
        chips_d = _table(data, "chips")
        _apply("chips", cfg.chips, chips_d, ("datasheet_dir",))
        raw_sources = chips_d.get("sources", [])
        if isinstance(raw_sources, list):
            cfg.chips.sources = [
                source for source in map(_chip_source_from_dict, raw_sources) if source
            ]
        # The switch written on [chips] itself, where it isn't a key. It's
        # per source — but someone wrote it to hold something back, so unless
        # it plainly says "on air" every source is kept off, and it's said.
        misplaced = sorted(k for k in chips_d if k == "on_air" or _meant_as_on_air(k))
        if misplaced:
            # Only "on air: yes" asks for nothing to be held back.
            holds_back = any(
                _air_key(key) != "onair" or _switch(chips_d[key]) is not True for key in misplaced
            )
            log.warning(
                "config: [chips] has %s, which belongs on a [[chips.sources]] entry%s",
                ", ".join(misplaced),
                "; keeping every chip source off the stream card" if holds_back else "",
            )
            if holds_back:
                for source in cfg.chips.sources:
                    source.on_air = False
        _apply("flash", cfg.flash, _table(data, "flash"), ("command", "programmer"))
        _apply("minipro", cfg.minipro, _table(data, "minipro"), ("command", "last_chip"))
        _apply(
            "now_playing",
            cfg.now_playing,
            _table(data, "now_playing"),
            ("enabled", "hide_when_paused", "label"),
        )
        _apply("performance", cfg.performance, _table(data, "performance"), ("opencv_threads",))
        _apply(
            "obs",
            cfg.obs,
            _table(data, "obs"),
            ("enabled", "host", "port", "password", "auto_switch"),
        )

        raw_tools = data.get("tools", [])
        if isinstance(raw_tools, list):
            cfg.tools = [
                ToolConfig(
                    name=t["name"],
                    command=t["command"],
                    terminal=_switch(t.get("terminal")) is True,
                    scene=t["scene"] if isinstance(t.get("scene"), str) else "",
                )
                for t in raw_tools
                if isinstance(t, Mapping)
                and isinstance(t.get("name"), str)
                and isinstance(t.get("command"), str)
            ]
        raw_cameras = data.get("cameras", [])
        if isinstance(raw_cameras, list):
            cfg.cameras = [c for c in map(_camera_from_dict, raw_cameras) if c is not None]
        return cfg

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict, dropping keys TOML can't represent (None)."""
        d = asdict(self)
        if d["shell"].get("layout") is None:
            d["shell"].pop("layout", None)
        # An empty list would be written as `tools = []`, and TOML then
        # refuses a `[[tools]]` block added by hand below it — which is how
        # config.example.toml says to add one. So no empty list is written.
        for owner, name in ((d, "tools"), (d, "cameras"), (d["chips"], "sources")):
            if not owner[name]:
                owner.pop(name)
        return d


def _table(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """The section ``key`` of ``data``; empty when it's missing or isn't a table."""
    value = data.get(key)
    return value if isinstance(value, Mapping) else {}


def _switch(value: Any) -> bool | None:
    """A true/false setting as written: ``true``/``false``, or ``1``/``0``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _fitted(current: Any, value: Any) -> tuple[bool, Any]:
    """``(usable, value)``: ``value`` as a replacement for ``current``, by type.

    A field keeps the type its default has, and what fits is what a person
    might reasonably have typed: ``1`` or ``0`` for a switch, a whole number
    where a decimal is expected, and a decimal where a whole number is — cut
    to the whole part, which is what happened to ``fps = 29.97`` before.
    """
    if isinstance(current, bool):
        switch = _switch(value)
        return switch is not None, switch
    if isinstance(value, bool):
        return False, value
    if isinstance(current, int | float):
        if not isinstance(value, int | float):
            return False, value
        try:
            number = float(value)
        except OverflowError:  # a whole number too big to be a setting
            return False, value
        if not math.isfinite(number):
            return False, value
        return True, number if isinstance(current, float) else int(value)
    if isinstance(current, list):
        return isinstance(value, list) and all(isinstance(v, str) for v in value), value
    # Text, or a field with no default (the saved window layout): text.
    return isinstance(value, str), value


def _apply(section: str, obj: Any, data: Mapping[str, Any], keys: tuple[str, ...]) -> None:
    """Copy present ``keys`` from ``data`` onto dataclass instance ``obj``.

    A value of the wrong type for its field is left out, so the default
    stands — and it's logged, since the next save writes that default back.
    ``section`` is the table's name, for the log line.
    """
    for key in keys:
        if key in data:
            current = getattr(obj, key)
            usable, value = _fitted(current, data[key])
            if usable:
                setattr(obj, key, value)
            else:
                log.warning(
                    "config: [%s] %s is the wrong kind of value; using %r",
                    section,
                    key,
                    current,
                )


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
    """Load config from ``path`` (or the default). Missing file → defaults.

    Raises :class:`ConfigError` when the file is there but can't be read or
    isn't valid TOML. It never returns defaults for a file it couldn't read:
    a caller that then saved would overwrite the file with them.
    """
    target = Path(path) if path is not None else default_config_path()
    try:
        # "utf-8-sig": a file saved by an editor that adds a byte-order mark
        # is still the same file.
        text = target.read_text("utf-8-sig")
    except FileNotFoundError:
        return Config()
    except UnicodeDecodeError as exc:
        raise ConfigError(target, f"can't be read: {exc}") from exc
    except OSError as exc:
        raise ConfigError(target, f"can't be read: {exc.strerror or exc}") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(target, str(exc) + _hint(str(exc), text)) from exc
    except (ValueError, RecursionError) as exc:
        # Not TOMLDecodeError, but the parser all the same: a number with
        # thousands of digits, or brackets nested hundreds deep.
        raise ConfigError(target, f"can't be parsed: {exc}") from exc
    try:
        return Config.from_dict(data)
    except Exception as exc:
        # from_dict is written not to raise. If it does anyway, that's still
        # "this file can't be used", not a traceback before the window.
        raise ConfigError(target, f"isn't in the shape benchhud expects: {exc!r}") from exc


_EMPTY_LIST_IN_THE_WAY = re.compile(r"Cannot mutate immutable namespace \('(\w+)',\)")


def _hint(parser_said: str, text: str) -> str:
    """Plain words for the one parser message that needs them.

    Configs saved before #475 hold a line like ``tools = []``. TOML won't take
    a ``[[tools]]`` block after one, and says so in a way nobody would guess.
    Only said when the file really has that line — the parser uses the same
    words for other mistakes, and "delete that line" would be wrong for them.
    """
    found = _EMPTY_LIST_IN_THE_WAY.search(parser_said)
    if found is None:
        return ""
    name = found.group(1)
    empty_list = rf"^[ \t]*{re.escape(name)}[ \t]*=[ \t]*\[[ \t]*\][ \t]*(#.*)?$"
    if re.search(empty_list, text, re.MULTILINE) is None:
        return ""
    return f". An earlier line sets `{name} = []`: delete that line to use [[{name}]] blocks"


def _storable(value: Any) -> Any:
    """``value`` with the characters a config file can't hold taken out of its text.

    Two can't make the trip. An escape character is written by tomlkit as
    ``\\e``, which ``tomllib`` refuses on the way back in. A lone surrogate
    can't be encoded at all. Neither means anything in a path, a name or a
    token, and both arrive by paste, so they're dropped — from the names of
    things too (a camera shot's name is a key in the file).
    """
    if isinstance(value, str):
        return "".join(ch for ch in value if ch != "\x1b" and not "\ud800" <= ch <= "\udfff")
    if isinstance(value, dict):
        return {_storable(key): _storable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_storable(item) for item in value]
    return value


def _write(target: Path, data: bytes) -> None:
    """Put ``data`` at ``target`` so that a failed write leaves the old file whole.

    Written to a new file beside the real one, then moved over it in one
    step. "The real one" is what a symlink points at: a ``config.toml`` that
    links into a dotfiles repo stays a link, and the file it points at is
    what's replaced.

    If a file can't be made beside it — the folder is read-only but the file
    isn't — it falls back to writing in place, as it always did.
    """
    real = Path(os.path.realpath(target))
    real.parent.mkdir(parents=True, exist_ok=True)
    beside = real.with_name(f".{real.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(beside, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError:
        fd = os.open(real, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(beside, real)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(beside)
        raise


def save(config: Config, path: str | os.PathLike[str] | None = None) -> Path:
    """Persist ``config`` to ``path`` (or the default). Returns the path written.

    The file is owner-only (``0600``): it holds the intake, bench-parts and
    bench-recon tokens. An existing file is tightened too.

    What's about to be written is read back first. If it wouldn't load,
    :class:`ConfigError` is raised and the file on disk is left as it was —
    a save never replaces a working config with one the next start can't read.
    A write that fails (``OSError``) leaves the old file whole as well.
    """
    target = Path(path) if path is not None else default_config_path()
    text = tomlkit.dumps(_storable(config.to_dict()))
    try:
        tomllib.loads(text)
        data = text.encode("utf-8")
    except (ValueError, RecursionError) as exc:  # TOMLDecodeError, UnicodeEncodeError
        raise ConfigError(target, f"not saved, it wouldn't load back: {exc}") from exc
    _write(target, data)
    with contextlib.suppress(OSError):
        os.chmod(target, 0o600)  # the temp file's mode came with it; this is the fallback's
    return target


# Type alias for the "persist this config" callback the shell invokes on close.
PersistCallback = Callable[[Config], None]
