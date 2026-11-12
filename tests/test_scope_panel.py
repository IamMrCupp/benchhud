"""Tests for the BGR→QImage helper and the scope panel."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtGui import QImage

from benchhud.capture.frame_source import SyntheticFrameSource
from benchhud.shell.panels.scope import ScopePanel
from benchhud.shell.qt_image import bgr_to_qimage


def test_bgr_to_qimage_dimensions_and_format() -> None:
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    image = bgr_to_qimage(frame)
    assert image.width() == 64
    assert image.height() == 48
    assert image.format() == QImage.Format.Format_BGR888  # no channel swap (#347)


def test_bgr_to_qimage_keeps_colours_right() -> None:
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # pure blue in BGR
    image = bgr_to_qimage(frame)
    color = image.pixelColor(0, 0)
    assert (color.red(), color.green(), color.blue()) == (0, 0, 255)


def test_bgr_to_qimage_rejects_bad_shape() -> None:
    with pytest.raises(ValueError, match="BGR frame"):
        bgr_to_qimage(np.zeros((10, 10), dtype=np.uint8))


def test_scope_panel_metadata(qtbot) -> None:
    panel = ScopePanel()
    qtbot.addWidget(panel)
    assert panel.panel_id == "scope"
    assert panel.objectName() == "panel__scope"


def test_scope_panel_renders_frame(qtbot) -> None:
    panel = ScopePanel()
    qtbot.addWidget(panel)
    assert panel.has_frame is False
    panel.update_frame(SyntheticFrameSource(64, 48).read())
    assert panel.has_frame is True


def test_scope_panel_ignores_none(qtbot) -> None:
    panel = ScopePanel()
    qtbot.addWidget(panel)
    panel.update_frame(None)
    assert panel.has_frame is False


def test_bgr_to_qimage_takes_a_non_contiguous_frame() -> None:
    wide = np.zeros((4, 8, 3), dtype=np.uint8)
    wide[:, ::2, 2] = 200  # red in every other column
    image = bgr_to_qimage(wide[:, ::2])  # a strided view, as a crop can be
    assert image.width() == 4
    assert image.pixelColor(3, 3).red() == 200


def test_bgr_to_qimage_outlives_its_frame() -> None:
    frame = np.full((2, 2, 3), 9, dtype=np.uint8)
    image = bgr_to_qimage(frame)
    frame[:] = 0
    assert image.pixelColor(0, 0).blue() == 9
