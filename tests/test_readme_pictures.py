"""The README's picture script still runs, and its heat lands where it says (#500).

``scripts/readme_pictures.py`` drives private pieces of the main window (the
demo tick, the composite view's frame). This runs it so a rename there fails
here, not the next time someone retakes the pictures.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from benchhud.overlay.transform import RegistrationTransform
from benchhud.thermal.analyze import hottest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "readme_pictures.py"


@pytest.fixture(scope="module")
def pictures():
    spec = importlib.util.spec_from_file_location("readme_pictures", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["readme_pictures"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("readme_pictures", None)


def test_the_heat_sits_on_the_drawn_chip(pictures) -> None:
    temps = pictures.board_heat()
    spot = hottest(temps)
    # Through the default registration, as the window places it.
    x, y = RegistrationTransform().map_point(
        spot.x, spot.y, (temps.shape[1], temps.shape[0]), pictures.SCOPE_SIZE
    )
    cx, cy, side = pictures.CHIP
    assert abs(x - cx) < side / 4
    assert abs(y - cy) < side / 4
    assert spot.value > 60.0  # over the default threshold, so the tint shows


def test_it_renders_every_picture(pictures, qapp, tmp_path) -> None:
    paths = pictures.main(tmp_path)

    assert [p.name for p in paths] == ["hud.png", "meter.png", "jobs.png", "thermal-modes.jpg"]
    for path in paths:
        image = QImage(str(path))
        assert not image.isNull(), path
        assert image.width() >= 600, path
