"""The thermal starts centred and filling the scope frame (#242)."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.overlay.config_adapter import config_from_transform, transform_from_config
from benchhud.overlay.transform import RegistrationTransform
from benchhud.shell.main_window import MainWindow

THERMAL = (256, 192)
SCOPE = (1280, 720)


def test_default_covers_and_centres() -> None:
    t = RegistrationTransform()
    assert t.map_point(128, 96, THERMAL, SCOPE) == pytest.approx((640, 360))  # centre on centre
    # cover: 5x wide (1280/256) beats 3.75x tall, so the width fills exactly
    left = t.map_point(0, 96, THERMAL, SCOPE)
    right = t.map_point(256, 96, THERMAL, SCOPE)
    assert left[0] == pytest.approx(0) and right[0] == pytest.approx(1280)


def test_the_warped_frame_has_no_empty_border() -> None:
    temps = np.full((THERMAL[1], THERMAL[0]), 30.0, dtype=np.float32)
    out = RegistrationTransform().warp(temps, SCOPE)
    assert out.shape == (720, 1280)
    assert out[5:-5, 5:-5].min() == pytest.approx(30.0)  # covered, no black margin


def test_scale_zooms_in_about_the_centre() -> None:
    t = RegistrationTransform(scale=2.0)
    assert t.map_point(128, 96, THERMAL, SCOPE) == pytest.approx((640, 360))
    assert t.map_point(192, 96, THERMAL, SCOPE)[0] == pytest.approx(640 + 64 * 10)


def test_offsets_are_nudges_from_centre() -> None:
    t = RegistrationTransform(offset_x=12, offset_y=-7)
    assert t.map_point(128, 96, THERMAL, SCOPE) == pytest.approx((652, 353))


def test_migration_puts_every_pixel_where_it_was() -> None:
    """The bench's saved values: no jump when they're converted."""
    old = RegistrationTransform(scale=3.0, offset_x=800, offset_y=400, rotation_deg=4.0, fit=False)
    new = old.migrated(THERMAL, SCOPE)
    k = RegistrationTransform.cover(THERMAL, SCOPE, 4.0)  # the fit follows the turn (#259)
    assert new.current and new.scale == pytest.approx(3.0 / k)
    for point in [(0, 0), (256, 0), (128, 96), (37, 150)]:
        assert new.map_point(*point, THERMAL, SCOPE) == pytest.approx(
            old.map_point(*point, THERMAL, SCOPE)
        )
    assert new.migrated(THERMAL, SCOPE) is new  # already converted


def test_config_without_a_version_is_the_old_placement() -> None:
    old = Config.from_dict({"registration": {"scale": 1.0, "offset_x": 800.0, "offset_y": 400.0}})
    assert old.registration.version == 1
    assert not transform_from_config(old.registration).fit
    assert Config().registration.version == 3  # a fresh install
    assert Config.from_dict({}).registration.version == 3
    assert config_from_transform(RegistrationTransform()).version == 3


def test_the_window_converts_an_old_transform_once(qtbot) -> None:
    config = Config.from_dict({"registration": {"offset_x": 800.0, "offset_y": 400.0}})
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    window.scope_panel.update_frame(np.zeros((SCOPE[1], SCOPE[0], 3), dtype=np.uint8))
    window.thermal_panel.update_temps(np.full((THERMAL[1], THERMAL[0]), 25.0, np.float32))
    window.render_composite()
    assert config.registration.version == 3
    assert config.registration.offset_x == pytest.approx(800 + 128 - 640)
    assert config.registration.scale == pytest.approx(1 / 5)
    assert window.controls_panel.current_transform().fit
