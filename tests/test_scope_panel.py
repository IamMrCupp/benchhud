"""Tests for the BGR→QImage helper and the scope panel."""

from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from benchhud.capture.frame_source import SyntheticFrameSource
from benchhud.shell.composite_view import CompositeView
from benchhud.shell.panels.scope import ScopePanel
from benchhud.shell.qt_image import bgr_to_fitted_pixmap, bgr_to_qimage, fitted_size


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


# --- the fitted path the views draw through (#347) ----------------------------

#: BGR values, each with a different number in every channel.
_BLUE, _GREEN, _RED, _MIXED = (255, 0, 0), (0, 255, 0), (0, 0, 255), (10, 120, 230)


def _quadrants(width: int = 640, height: int = 480) -> np.ndarray:
    """A frame of four flat colours: blue, green / red, mixed."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[: height // 2, : width // 2] = _BLUE
    frame[: height // 2, width // 2 :] = _GREEN
    frame[height // 2 :, : width // 2] = _RED
    frame[height // 2 :, width // 2 :] = _MIXED
    return frame


def _assert_quadrant_colours(image: QImage) -> None:
    """The middle of each quarter, read as RGB, is the frame's colour there."""
    width, height = image.width(), image.height()
    centres = {
        (width // 4, height // 4): _BLUE,
        (3 * width // 4, height // 4): _GREEN,
        (width // 4, 3 * height // 4): _RED,
        (3 * width // 4, 3 * height // 4): _MIXED,
    }
    for (x, y), (blue, green, red) in centres.items():
        colour = image.pixelColor(x, y)
        assert (colour.red(), colour.green(), colour.blue()) == (red, green, blue)


@pytest.mark.parametrize("target", [QSize(160, 120), QSize(640, 480), QSize(1280, 960), QSize()])
def test_fitted_pixmap_keeps_colours_at_any_size(qtbot, target: QSize) -> None:
    """Shrunk, same size, enlarged, or no view size yet: the colours are the frame's."""
    pixmap = bgr_to_fitted_pixmap(_quadrants(), target)
    expected = (640, 480) if target.isEmpty() else (target.width(), target.height())
    assert (pixmap.width(), pixmap.height()) == expected
    _assert_quadrant_colours(pixmap.toImage())


def test_fitted_pixmap_matches_the_copying_path(qtbot) -> None:
    """Same pixels as the QImage the views used to build, when nothing is resized."""
    frame = np.random.default_rng(347).integers(0, 256, (48, 64, 3), dtype=np.uint8)
    fitted = bgr_to_fitted_pixmap(frame, QSize()).toImage()
    reference = bgr_to_qimage(frame).convertToFormat(fitted.format())
    assert fitted == reference


def test_fitted_pixmap_keeps_the_aspect_ratio(qtbot) -> None:
    pixmap = bgr_to_fitted_pixmap(np.zeros((720, 1280, 3), dtype=np.uint8), QSize(640, 480))
    assert (pixmap.width(), pixmap.height()) == (640, 360)
    assert fitted_size((1280, 720), QSize(640, 480)) == (640, 360)


def test_fitted_pixmap_outlives_its_frame(qtbot) -> None:
    """The pixmap owns its pixels: reusing the frame's memory can't change it."""
    frame = np.full((4, 4, 3), 9, dtype=np.uint8)
    pixmap = bgr_to_fitted_pixmap(frame, QSize())
    frame[:] = 0
    del frame
    assert pixmap.toImage().pixelColor(0, 0).blue() == 9


def test_fitted_pixmap_rejects_bad_shape(qtbot) -> None:
    with pytest.raises(ValueError, match="BGR frame"):
        bgr_to_fitted_pixmap(np.zeros((10, 10), dtype=np.uint8), QSize(4, 4))


def test_scope_panel_shows_the_frames_colours(qtbot) -> None:
    panel = ScopePanel()
    qtbot.addWidget(panel)
    panel.resize(320, 240)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_frame(_quadrants())
    _assert_quadrant_colours(panel._view.pixmap().toImage())


def test_composite_view_shows_the_frames_colours(qtbot) -> None:
    view = CompositeView()
    qtbot.addWidget(view)
    view.resize(320, 240)
    view.show()
    qtbot.waitExposed(view)
    view.show_frame(_quadrants())
    _assert_quadrant_colours(view._label.pixmap().toImage())


def test_a_hidden_scope_panel_draws_nothing_until_shown(qtbot) -> None:
    """A closed or tabbed-away dock costs no drawing, and catches up when shown."""
    panel = ScopePanel()
    qtbot.addWidget(panel)
    panel.resize(320, 240)
    panel.update_frame(_quadrants())
    assert panel.has_frame is True  # it has the frame (capture-to-job still works)
    assert panel._view.pixmap().isNull()  # but drew nothing
    panel.show()
    qtbot.waitExposed(panel)
    _assert_quadrant_colours(panel._view.pixmap().toImage())


def test_a_hidden_composite_view_draws_nothing_until_shown(qtbot) -> None:
    view = CompositeView()
    qtbot.addWidget(view)
    view.resize(320, 240)
    view.show_frame(_quadrants())
    assert view.has_frame is True
    assert view._label.pixmap().isNull()
    view.show()
    qtbot.waitExposed(view)
    _assert_quadrant_colours(view._label.pixmap().toImage())
