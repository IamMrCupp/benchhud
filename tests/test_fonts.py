"""Tests for bundled-font registration and the meter's LCD font stack."""

from __future__ import annotations

from importlib.resources import files

from benchhud.shell.fonts import seven_segment_family
from benchhud.shell.panels.power import _build_style, _lcd_font_family


def test_font_asset_and_license_are_bundled() -> None:
    """The .ttf and its OFL license must ship in the package."""
    fonts = files("benchhud.resources").joinpath("fonts")
    assert fonts.joinpath("DSEG7Classic-Bold.ttf").is_file()
    assert fonts.joinpath("OFL.txt").is_file()


def test_seven_segment_font_registers(qtbot) -> None:
    """Under a QApplication the bundled font loads as 'DSEG7 Classic'."""
    assert seven_segment_family() == "DSEG7 Classic"


def test_lcd_stack_leads_with_seven_segment_when_present() -> None:
    stack = _lcd_font_family("DSEG7 Classic")
    assert stack.startswith('"DSEG7 Classic",')
    assert "monospace" in stack  # still falls through for glyphs DSEG lacks


def test_lcd_stack_is_monospace_only_without_the_font() -> None:
    stack = _lcd_font_family(None)
    assert "DSEG" not in stack
    assert stack.endswith("monospace")


def test_style_uses_the_lcd_font_for_numeric_readouts() -> None:
    style = _build_style("DSEG7 Classic")
    # Both the DMM primary reading and the grid values get the 7-seg face.
    assert style.count('"DSEG7 Classic"') == 2
    assert "#meterValue" in style
    assert "#meterPrimary" in style


def test_style_falls_back_without_the_font() -> None:
    style = _build_style(None)
    assert "DSEG" not in style
    assert "monospace" in style
