"""Tests for the on-stream telemetry overlay (hardware-free).

The pure layers (model building, numpy blit) need no display; the Qt rendering
and the MainWindow wiring run under the offscreen platform via ``qtbot``.
"""

from __future__ import annotations

import numpy as np

from benchhud.config import Config, TelemetryOverlayConfig
from benchhud.instruments.owon_spm8104 import DmmReading
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.overlay.telemetry import (
    MeterReadout,
    blit_rgba,
    build_model,
    draw_telemetry_overlay,
    render_overlay_image,
)
from benchhud.shell.main_window import MainWindow

_READOUT = MeterReadout(
    voltage="12.30",
    current="0.45",
    power="5.5",
    max_current="0.60",
    dmm_mode="VDC",
    dmm_value="3.300",
    dmm_unit="V",
)


# --- build_model (pure) --------------------------------------------------


def test_build_model_shows_dmm_and_all_psu_cells() -> None:
    model = build_model(_READOUT, TelemetryOverlayConfig(show_psu=True, show_dmm=True))
    assert model.dmm == ("VDC", "3.300", "V")
    assert [c.caption for c in model.cells] == [
        "VOLTAGE (V)",
        "CURRENT (A)",
        "POWER (W)",
        "MAX CURRENT (A)",
    ]
    assert [c.value for c in model.cells] == ["12.30", "0.45", "5.5", "0.60"]
    assert not model.is_empty


def test_build_model_hides_dmm() -> None:
    model = build_model(_READOUT, TelemetryOverlayConfig(show_psu=True, show_dmm=False))
    assert model.dmm is None
    assert len(model.cells) == 4


def test_build_model_hides_psu() -> None:
    model = build_model(_READOUT, TelemetryOverlayConfig(show_psu=False, show_dmm=True))
    assert model.dmm is not None
    assert model.cells == ()


def test_build_model_empty_when_both_hidden() -> None:
    model = build_model(_READOUT, TelemetryOverlayConfig(show_psu=False, show_dmm=False))
    assert model.is_empty


# --- blit_rgba (pure numpy) ----------------------------------------------


def _frame(h: int = 100, w: int = 200) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _overlay(h: int, w: int, bgr=(10, 20, 30), alpha: int = 255) -> np.ndarray:
    over = np.zeros((h, w, 4), dtype=np.uint8)
    over[:, :, 0], over[:, :, 1], over[:, :, 2] = bgr
    over[:, :, 3] = alpha
    return over


def test_blit_opaque_replaces_pixels_and_leaves_input_untouched() -> None:
    frame = _frame()
    out = blit_rgba(frame, _overlay(10, 10), corner="top-left", margin=0)
    assert tuple(out[0, 0]) == (10, 20, 30)
    assert out[50, 50].tolist() == [0, 0, 0]  # untouched elsewhere
    assert frame[0, 0].tolist() == [0, 0, 0]  # original not mutated
    assert out is not frame


def test_blit_transparent_overlay_is_a_noop_copy() -> None:
    frame = np.full((40, 40, 3), 7, dtype=np.uint8)
    out = blit_rgba(frame, _overlay(10, 10, alpha=0), corner="top-left", margin=0)
    assert np.array_equal(out, frame)


def test_blit_half_alpha_blends() -> None:
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    out = blit_rgba(frame, _overlay(5, 5, bgr=(200, 200, 200), alpha=128), "top-left", 0)
    # 200*0.502 + 0*0.498 ≈ 100
    assert 98 <= out[0, 0, 0] <= 102


def test_blit_corner_placement() -> None:
    out = blit_rgba(_frame(100, 200), _overlay(10, 10), corner="bottom-right", margin=5)
    assert tuple(out[100 - 5 - 1, 200 - 5 - 1]) == (10, 20, 30)  # near bottom-right
    assert out[0, 0].tolist() == [0, 0, 0]  # top-left clear


def test_blit_oversized_overlay_is_cropped_not_fatal() -> None:
    out = blit_rgba(_frame(20, 20), _overlay(500, 500), corner="top-left", margin=0)
    assert out.shape == (20, 20, 3)


# --- render + end-to-end (Qt) --------------------------------------------


def test_render_overlay_image_has_size(qtbot) -> None:
    model = build_model(_READOUT, TelemetryOverlayConfig())
    image = render_overlay_image(model)
    assert not image.isNull()
    assert image.width() > 0 and image.height() > 0


def test_render_empty_model_is_null(qtbot) -> None:
    model = build_model(_READOUT, TelemetryOverlayConfig(show_psu=False, show_dmm=False))
    image = render_overlay_image(model)
    assert image.isNull()


def test_draw_telemetry_overlay_changes_the_frame(qtbot) -> None:
    frame = _frame(300, 400)
    out = draw_telemetry_overlay(frame, _READOUT, TelemetryOverlayConfig(corner="bottom-left"))
    assert out.shape == frame.shape
    assert not np.array_equal(out, frame)  # something got drawn


def test_draw_telemetry_overlay_hidden_returns_same_frame(qtbot) -> None:
    frame = _frame(300, 400)
    hidden = TelemetryOverlayConfig(show_psu=False, show_dmm=False)
    out = draw_telemetry_overlay(frame, _READOUT, hidden)
    assert out is frame  # nothing to draw → untouched original


# --- MainWindow wiring ---------------------------------------------------


def test_overlay_off_by_default_leaves_frame_untouched(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    frame = _frame(200, 320)
    assert window._apply_telemetry_overlay(frame) is frame


def test_overlay_on_burns_readout_into_frame(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window._config.telemetry_overlay.enabled = True
    window.power_panel.update_from_snapshot(
        TelemetrySnapshot(
            dmm=DmmReading(mode="VDC", value=3.3, status=None, ranging="Auto", raw=""),
            voltage=12.0,
            current=0.5,
        )
    )
    frame = _frame(300, 480)
    out = window._apply_telemetry_overlay(frame)
    assert not np.array_equal(out, frame)


def test_meter_readout_reflects_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.power_panel.update_from_snapshot(TelemetrySnapshot(voltage=12.0, current=0.5))
    readout = window._meter_readout()
    assert readout.voltage == "12.00"
    assert readout.current == "0.50"
    assert readout.power == "6.0"


# --- config round-trip ---------------------------------------------------


def test_telemetry_overlay_config_round_trips() -> None:
    cfg = Config()
    cfg.telemetry_overlay.enabled = True
    cfg.telemetry_overlay.corner = "top-right"
    cfg.telemetry_overlay.show_dmm = False
    restored = Config.from_dict(cfg.to_dict())
    assert restored.telemetry_overlay.enabled is True
    assert restored.telemetry_overlay.corner == "top-right"
    assert restored.telemetry_overlay.show_dmm is False


def test_telemetry_overlay_defaults_when_absent() -> None:
    cfg = Config.from_dict({})
    assert cfg.telemetry_overlay.enabled is False
    assert cfg.telemetry_overlay.corner == "bottom-left"
    assert cfg.telemetry_overlay.show_psu is True


# --- scaling + opacity (§C.2 polish) -------------------------------------


def test_auto_scale_tracks_frame_width() -> None:
    from benchhud.overlay.telemetry import auto_scale

    assert auto_scale(1280) == 1.0
    assert auto_scale(1920) > 1.0
    assert auto_scale(640) < 1.0


def test_auto_scale_is_clamped() -> None:
    from benchhud.overlay.telemetry import auto_scale

    assert auto_scale(100) == 0.5  # never vanishingly small
    assert auto_scale(10000) == 2.5  # never absurdly large
    assert auto_scale(0) == 1.0  # degenerate frame


def test_render_scales_the_panel(qtbot) -> None:
    model = build_model(_READOUT, TelemetryOverlayConfig())
    small = render_overlay_image(model, 1.0)
    big = render_overlay_image(model, 2.0)
    assert big.width() > small.width()
    assert big.height() > small.height()


def test_wider_frame_gets_a_bigger_overlay(qtbot) -> None:
    """The gap this fixes: a fixed-size readout looked tiny on a 1920 feed."""
    narrow = draw_telemetry_overlay(_frame(540, 960), _READOUT, TelemetryOverlayConfig())
    wide = draw_telemetry_overlay(_frame(1080, 1920), _READOUT, TelemetryOverlayConfig())

    def _painted(frame):
        return int((frame != 0).any(axis=2).sum())

    # Proportionally, the readout should cover a comparable share of each frame.
    narrow_share = _painted(narrow) / (540 * 960)
    wide_share = _painted(wide) / (1080 * 1920)
    assert abs(narrow_share - wide_share) < 0.05


def test_opacity_lightens_the_overlay() -> None:
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    over = _overlay(10, 10, bgr=(200, 200, 200), alpha=255)
    solid = blit_rgba(frame, over, "top-left", 0, opacity=1.0)
    faded = blit_rgba(frame, over, "top-left", 0, opacity=0.5)
    assert faded[0, 0, 0] < solid[0, 0, 0]


def test_opacity_zero_leaves_the_frame_alone() -> None:
    frame = np.full((30, 30, 3), 9, dtype=np.uint8)
    out = blit_rgba(frame, _overlay(10, 10), "top-left", 0, opacity=0.0)
    assert np.array_equal(out, frame)


def test_scale_and_opacity_round_trip_in_config() -> None:
    cfg = Config()
    cfg.telemetry_overlay.scale = 1.5
    cfg.telemetry_overlay.opacity = 0.7
    restored = Config.from_dict(cfg.to_dict())
    assert restored.telemetry_overlay.scale == 1.5
    assert restored.telemetry_overlay.opacity == 0.7
