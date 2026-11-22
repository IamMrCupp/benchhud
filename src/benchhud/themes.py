"""Appearance: the light and dark looks, and custom themes read from a folder.

benchhud's look is burnwright's: the same ``--bw-*`` colours, light and dark,
so the HUD, burnwright and the web tools round the bench (intake, recon,
parts) read as one family. ``[shell] appearance`` picks one of

* ``"system"``: light or dark, following the operating system,
* ``"light"`` or ``"dark"``,
* ``"theme:<file>"``: a theme file from the themes folder.

A theme is one JSON file in burnwright's format, so a file written for
burnwright or the web tools drops straight in::

    {"name": "Amber terminal", "base": "dark", "colors": {"bg": "#120d02", ...}}

They live in ``$BENCHHUD_THEMES_DIR``, else a ``themes`` folder beside the
config file. **Colours and nothing else**: names are checked against
burnwright's list and values against a strict idea of a colour (hex, or
``rgb()`` / ``hsl()`` with plain numbers), then turned into numbers here, so
nothing from a file reaches the stylesheet as text.

Qt-free on purpose, like :mod:`benchhud.palette`: tests and the shell both
import it.
"""

from __future__ import annotations

import colorsys
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: An RGBA colour: three 0-255 channels and an alpha from 0 to 1.
RGBA = tuple[int, int, int, float]

THEMES_DIR_ENV = "BENCHHUD_THEMES_DIR"

#: What ``[shell] appearance`` is when the config doesn't say. benchhud was
#: dark-only before this, and a stream rig shouldn't change look on its own
#: the first time it starts with a light-mode OS.
DEFAULT_APPEARANCE = "dark"
LOOKS = ("system", "light", "dark")
THEME_PREFIX = "theme:"

#: Every colour a theme can set: burnwright's ``--bw-*`` properties without
#: the prefix. The whole list, so a burnwright theme file reads here without
#: complaint; the ones benchhud has no use for (the hex editor's, the diff
#: view's) are accepted and left alone.
THEME_PROPERTIES = (
    "bg",
    "panel",
    "border",
    "fg",
    "muted",
    "control-bg",
    "control-border",
    "control-hover",
    "accent",
    "accent-soft",
    "danger",
    "danger-soft",
    "warning",
    "warning-soft",
    "warning-border",
    "ok",
    "ok-soft",
    "backdrop",
    "shadow",
    "hex-bg",
    "hex-header-bg",
    "hex-muted",
    "hex-edited-fg",
    "hex-edited-bg",
    "hex-selected-bg",
    "hex-cursor",
    "hex-cursor-fg",
    "hex-cursor-dim",
    "diff-changed-bg",
    "diff-changed-fg",
    "diff-extra-bg",
    "diff-extra-fg",
    "diff-missing-bg",
    "diff-missing-fg",
)

#: Which of benchhud's colours each burnwright property sets.
PROPERTY_COLOURS: dict[str, tuple[str, ...]] = {
    "bg": ("window",),
    "panel": ("panel",),
    "border": ("border",),
    "fg": ("text", "bright"),
    "muted": ("muted",),
    "control-bg": ("raised",),
    "control-border": ("edge",),
    "control-hover": ("hover",),
    "accent": ("accent",),
    "accent-soft": ("accent-soft",),
    "danger": ("danger",),
    "danger-soft": ("danger-soft",),
    "warning": ("warning",),
    "warning-soft": ("warning-soft",),
    "warning-border": ("warning-edge",),
    "ok": ("ok",),
    "ok-soft": ("ok-soft",),
}

#: burnwright's dark set (``:root[data-theme="dark"]`` in its App.css), with
#: the few in-between shades a desktop app needs. The values benchhud has
#: always had.
DARK = {
    "window": "#151517",  # behind everything: the gaps between panels (--bw-bg)
    "chrome": "#111113",  # menu bar, status bar, the composite's surround
    "panel": "#1f1f22",  # a dock's body (--bw-panel)
    "raised": "#2a2a2e",  # cards, fields, buttons (--bw-control-bg)
    "hover": "#34343a",  # (--bw-control-hover)
    "border": "#3a3a40",  # round a panel (--bw-border)
    "edge": "#55555d",  # round a field or a button (--bw-control-border)
    "text": "#e4e4e8",
    "bright": "#f4f4f6",  # (--bw-fg is between the two)
    "muted": "#a3a3ab",  # (--bw-muted)
    "faint": "#6b6b73",
    "accent": "#8fb0ff",  # (--bw-accent)
    "accent-soft": "#24345c",  # (--bw-accent-soft)
    "accent-ink": "#10162a",  # text on a filled accent
    "danger": "#ff9a92",
    "danger-soft": "#4a1f1c",
    "danger-dim": "#7d524f",  # danger at half strength, for a disabled button
    "warning": "#ffc66b",
    "warning-soft": "#3d2e10",
    "warning-edge": "#f59e0b",  # (--bw-warning-border)
    "ok": "#7fd6a0",
    "ok-soft": "#173624",
}

#: burnwright's light set (``:root`` in its App.css), shaded the same way.
LIGHT = {
    "window": "#f1f1f4",
    "chrome": "#e6e6ea",
    "panel": "#ffffff",
    "raised": "#ffffff",
    "hover": "#f0f2f8",
    "border": "#d2d2d8",
    "edge": "#b9b9c2",
    "text": "#1d1d1f",
    "bright": "#0b0b0c",
    "muted": "#5c5c63",
    "faint": "#9a9aa2",
    "accent": "#2f5fcf",
    "accent-soft": "#e3ebff",
    "accent-ink": "#ffffff",
    "danger": "#b3261e",
    "danger-soft": "#fdecea",
    "danger-dim": "#d9928e",
    "warning": "#8a4b06",
    "warning-soft": "#fff5e0",
    "warning-edge": "#b45309",
    "ok": "#1b6b3a",
    "ok-soft": "#e8f5ec",
}

BASES = {"light": LIGHT, "dark": DARK}

NAME_LIMIT = 60
VALUE_LIMIT = 64
#: A themes folder with more than this many files is somebody's mistake.
FILE_LIMIT = 50
SIZE_LIMIT = 64 * 1024

_NUMBER = r"(?:none|[-+]?(?:\d+\.?\d*|\.\d+)(?:%|deg|grad|rad|turn)?)"
_SEPARATOR = r"(?:\s*,\s*|\s*/\s*|\s+)"
_HEX = re.compile(r"^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE)
_FUNCTION = re.compile(
    rf"^(rgb|hsl)a?\(\s*({_NUMBER}(?:{_SEPARATOR}{_NUMBER}){{2,3}})\s*\)$", re.IGNORECASE
)
_PART = re.compile(_NUMBER, re.IGNORECASE)


# --- colours ------------------------------------------------------------------


def parse_colour(value: object) -> RGBA | None:
    """``value`` as RGBA, or None when it isn't a colour a theme may use.

    Narrower than CSS on purpose: hex, ``rgb()`` and ``hsl()`` (with or
    without the ``a``, commas or spaces) and nothing else. No names, no
    ``var()``, no ``url()``.
    """
    if not isinstance(value, str) or len(value) > VALUE_LIMIT:
        return None
    text = value.strip()
    if _HEX.match(text):
        digits = text[1:]
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        a = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (r, g, b, round(a, 3))
    match = _FUNCTION.match(text)
    if not match:
        return None
    parts = _PART.findall(match.group(2))
    try:
        if match.group(1).lower() == "rgb":
            r, g, b = (_channel(p) for p in parts[:3])
        else:
            r, g, b = _hsl(*parts[:3])
        a = _alpha(parts[3]) if len(parts) == 4 else 1.0
    except ValueError:
        return None
    return (r, g, b, a)


def _number(part: str) -> float:
    if part.lower() == "none":
        return 0.0
    value = float(re.sub(r"[a-z%]+$", "", part, flags=re.IGNORECASE))
    if not math.isfinite(value):
        raise ValueError(part)
    return value


def _channel(part: str) -> int:
    value = _number(part)
    if part.endswith("%"):
        value = value * 255 / 100
    return round(min(255.0, max(0.0, value)))


def _alpha(part: str) -> float:
    value = _number(part)
    if part.endswith("%"):
        value /= 100
    return round(min(1.0, max(0.0, value)), 3)


def _hsl(h: str, s: str, lightness: str) -> tuple[int, int, int]:
    hue = _number(h)
    unit = re.search(r"(deg|grad|rad|turn)$", h, re.IGNORECASE)
    unit_name = unit.group(1).lower() if unit else "deg"
    turns = {
        "deg": hue / 360,
        "grad": hue / 400,
        "rad": hue / (2 * math.pi),
        "turn": hue,
    }[unit_name] % 1.0
    sat = min(100.0, max(0.0, _number(s))) / 100
    light = min(100.0, max(0.0, _number(lightness))) / 100
    r, g, b = colorsys.hls_to_rgb(turns, light, sat)
    return round(r * 255), round(g * 255), round(b * 255)


def qss_colour(colour: RGBA) -> str:
    """How a stylesheet writes ``colour``: hex, or ``rgba()`` when it's see-through."""
    r, g, b, a = colour
    if a >= 1.0:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r}, {g}, {b}, {round(a * 100)}%)"


def _mix(colour: RGBA, other: RGBA, amount: float) -> RGBA:
    """``colour`` moved toward ``other`` by ``amount`` (0 = unchanged)."""
    r, g, b = (round(c + (o - c) * amount) for c, o in zip(colour[:3], other[:3], strict=True))
    return (r, g, b, colour[3])


def luminance(colour: RGBA) -> float:
    """Relative luminance, 0 (black) to 1 (white), as WCAG reckons it."""

    def linear(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in colour[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


_BLACK: RGBA = (0, 0, 0, 1.0)
_WHITE: RGBA = (255, 255, 255, 1.0)


# --- theme files --------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    """A theme file that was read and can be used."""

    #: The file's name, ``amber.json``: what the config stores the choice by.
    id: str
    name: str
    base: str
    #: burnwright property name → colour.
    colours: dict[str, RGBA] = field(default_factory=dict)
    #: What the file had that wasn't taken, in words, for whoever wrote it.
    ignored: tuple[str, ...] = ()

    @property
    def choice(self) -> str:
        return THEME_PREFIX + self.id


@dataclass(frozen=True)
class BrokenTheme:
    file: str
    reason: str


def parse_theme(file: str, text: str) -> Theme | BrokenTheme:
    """One theme file: a usable theme, or why it isn't one."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        return BrokenTheme(file, f"it isn't valid JSON ({exc})")
    if not isinstance(data, dict):
        return BrokenTheme(file, "a theme is a JSON object: { … }")

    base = data.get("base")
    if base not in BASES:
        return BrokenTheme(file, '"base" has to be "light" or "dark"')
    colours = data.get("colors", {})
    if not isinstance(colours, dict):
        return BrokenTheme(file, '"colors" has to be an object: { "bg": "#101418", … }')

    ignored: list[str] = []
    name = data.get("name")
    shown = re.sub(r"\.json$", "", file, flags=re.IGNORECASE) or file
    if isinstance(name, str) and name.strip():
        shown = name.strip()[:NAME_LIMIT]
    elif name is not None:
        ignored.append("\"name\" isn't text, so the file's name is used")
    for key in data:
        if key not in ("name", "base", "colors"):
            ignored.append(f'"{key}" isn\'t part of a theme')

    taken: dict[str, RGBA] = {}
    for key, value in colours.items():
        prop = key.removeprefix("--bw-") if isinstance(key, str) else None
        if prop not in THEME_PROPERTIES:
            ignored.append(f'"{key}" isn\'t a theme colour')
            continue
        colour = parse_colour(value)
        if colour is None:
            ignored.append(f'"{key}" isn\'t a colour a theme can use')
        else:
            taken[prop] = colour

    return Theme(id=file, name=shown, base=base, colours=taken, ignored=tuple(ignored))


def themes_dir(config_path: Path, environ: Mapping[str, str] | None = None) -> Path:
    """``$BENCHHUD_THEMES_DIR``, else ``themes`` beside the config file."""
    env = os.environ if environ is None else environ
    override = env.get(THEMES_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return config_path.parent / "themes"


def load_themes(folder: Path) -> tuple[list[Theme], list[BrokenTheme]]:
    """Every ``*.json`` in ``folder``, sorted by name. A missing folder is no themes."""
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".json")
    except OSError:
        return [], []
    themes: list[Theme] = []
    broken: list[BrokenTheme] = []
    for path in files[:FILE_LIMIT]:
        try:
            if path.stat().st_size > SIZE_LIMIT:
                broken.append(BrokenTheme(path.name, "it's too big to be a theme"))
                continue
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            broken.append(BrokenTheme(path.name, f"it couldn't be read ({exc.__class__.__name__})"))
            continue
        parsed = parse_theme(path.name, text)
        (themes if isinstance(parsed, Theme) else broken).append(parsed)
    themes.sort(key=lambda t: t.name.lower())
    return themes, broken


# --- resolving a choice to colours --------------------------------------------


@dataclass(frozen=True)
class Look:
    """What a choice comes to: light or dark underneath, and every colour."""

    base: str
    colours: dict[str, RGBA]
    #: The theme in use, if the choice was one.
    theme: Theme | None = None

    def qss(self) -> dict[str, str]:
        return {name: qss_colour(colour) for name, colour in self.colours.items()}

    @property
    def dark_check(self) -> bool:
        """Whether a check mark on the accent should be dark (the accent is light)."""
        return luminance(self.colours["accent"]) > 0.35


def _base_colours(base: str) -> dict[str, RGBA]:
    parsed = {name: parse_colour(value) for name, value in BASES[base].items()}
    return {name: colour for name, colour in parsed.items() if colour is not None}


def with_theme(base: str, theme_colours: Mapping[str, RGBA]) -> dict[str, RGBA]:
    """``base``'s colours with a theme's laid over them.

    The in-between shades a theme file can't name (the menu bar's, a disabled
    danger button's, the ink on a filled accent) follow the colours they're
    made from, so a theme that recolours the background gets a menu bar to
    match. One that doesn't touch them keeps the base's.
    """
    colours = _base_colours(base)
    for prop, colour in theme_colours.items():
        for name in PROPERTY_COLOURS.get(prop, ()):
            colours[name] = colour
    touched = set(theme_colours)
    if "bg" in touched:
        colours["chrome"] = _mix(colours["window"], _BLACK, 0.2 if base == "dark" else 0.05)
    if touched & {"muted", "panel"}:
        colours["faint"] = _mix(colours["muted"], colours["panel"], 0.4)
    if "accent" in touched:
        accent = colours["accent"]
        colours["accent-ink"] = _mix(accent, _BLACK, 0.85) if luminance(accent) > 0.35 else _WHITE
    if touched & {"danger", "panel"}:
        colours["danger-dim"] = _mix(colours["danger"], colours["panel"], 0.5)
    return colours


def normalise_choice(choice: object) -> str:
    """A stored appearance, or the default when it isn't one benchhud knows the shape of."""
    if isinstance(choice, str):
        choice = choice.strip()
        if choice in LOOKS or (choice.startswith(THEME_PREFIX) and len(choice) > len(THEME_PREFIX)):
            return choice
    return DEFAULT_APPEARANCE


def resolve(choice: str, *, system_dark: bool, themes: list[Theme]) -> Look:
    """The look ``choice`` comes to. A theme whose file has gone falls back to dark."""
    choice = normalise_choice(choice)
    if choice.startswith(THEME_PREFIX):
        wanted = choice[len(THEME_PREFIX) :]
        for theme in themes:
            if theme.id == wanted:
                return Look(theme.base, with_theme(theme.base, theme.colours), theme)
        choice = DEFAULT_APPEARANCE
    if choice == "system":
        choice = "dark" if system_dark else "light"
    return Look(choice, _base_colours(choice))
