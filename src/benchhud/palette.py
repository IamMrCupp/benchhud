"""One colour per measured quantity, shared by every surface that shows it.

Voltage, current and power each get a colour. The meter cells are tinted with
it, and the trend lines (dock chart and on-stream graph) are drawn in it, so a
viewer can match a number to its line at a glance. Max current is a current, so
it takes the current colour.

Qt-free on purpose: the overlays, the dock and tests all import it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeriesPalette:
    """Hex colours for each quantity (``#rrggbb``)."""

    voltage: str
    current: str
    power: str

    @property
    def max_current(self) -> str:
        return self.current


PALETTES: dict[str, SeriesPalette] = {
    # Oscilloscope channel colours (CH1 yellow, CH2 cyan, CH3 magenta): the
    # convention an electronics audience already reads without a legend.
    "scope": SeriesPalette(voltage="#f2c200", current="#00b8d9", power="#d63fbf"),
    # Okabe-Ito: designed to stay distinct under the common colour-vision
    # deficiencies.
    "okabe-ito": SeriesPalette(voltage="#e69f00", current="#56b4e9", power="#cc79a7"),
    # The dock chart's original QtCharts dark-theme colours.
    "qtcharts": SeriesPalette(voltage="#38ad6b", current="#3b8dc4", power="#eb8817"),
}

DEFAULT_PALETTE = "scope"


def palette(name: str = DEFAULT_PALETTE) -> SeriesPalette:
    """The named palette, or the default for an unknown name."""
    return PALETTES.get(name, PALETTES[DEFAULT_PALETTE])


def tint(colour: str, amount: float) -> str:
    """Mix ``colour`` toward white by ``amount`` (0 = unchanged, 1 = white)."""
    amount = min(1.0, max(0.0, amount))
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    mixed = (round(c + (255 - c) * amount) for c in (r, g, b))
    return "#" + "".join(f"{c:02x}" for c in mixed)


def shade(colour: str, amount: float) -> str:
    """Mix ``colour`` toward black by ``amount`` (0 = unchanged, 1 = black)."""
    amount = min(1.0, max(0.0, amount))
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    mixed = (round(c * (1.0 - amount)) for c in (r, g, b))
    return "#" + "".join(f"{c:02x}" for c in mixed)
