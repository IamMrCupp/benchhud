"""One colour per quantity, shared by the meter, the chart and the stream overlays."""

from __future__ import annotations

from benchhud.config import TelemetryOverlayConfig
from benchhud.overlay.telemetry import MeterReadout, build_model
from benchhud.palette import DEFAULT_PALETTE, PALETTES, palette, shade, tint


def test_default_is_the_scope_channel_palette():
    assert DEFAULT_PALETTE == "scope"
    scope = palette()
    assert (scope.voltage, scope.current, scope.power) == ("#f2c200", "#00b8d9", "#d63fbf")


def test_quantities_are_distinct_colours():
    for colours in PALETTES.values():
        assert len({colours.voltage, colours.current, colours.power}) == 3


def test_max_current_shares_the_current_colour():
    assert palette().max_current == palette().current


def test_unknown_palette_falls_back_to_default():
    assert palette("nope") == palette()


def test_tint_and_shade_mix_toward_white_and_black():
    assert tint("#000000", 1.0) == "#ffffff"
    assert tint("#204060", 0.0) == "#204060"
    assert tint("#000000", 0.5) == "#808080"
    assert shade("#ffffff", 1.0) == "#000000"
    assert shade("#804020", 0.5) == "#402010"


def test_meter_overlay_cells_carry_their_quantity_colour():
    model = build_model(MeterReadout(), TelemetryOverlayConfig())
    colours = palette()
    assert [c.colour for c in model.cells] == [
        colours.voltage,
        colours.current,
        colours.power,
        colours.max_current,
    ]


def test_dock_chart_lines_use_the_palette(qtbot):
    from benchhud.shell.power_chart import PowerChart

    chart = PowerChart()
    qtbot.addWidget(chart)
    colours = palette()
    assert chart.v_series.pen().color().name() == colours.voltage
    assert chart.a_series.pen().color().name() == colours.current
    assert chart.w_series.pen().color().name() == colours.power
