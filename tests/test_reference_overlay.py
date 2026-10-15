"""Comparing the scope with a known-good board (#256)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.overlay.reference import BLINK_SECONDS, CompareMode, ReferenceSettings, compare
from benchhud.shell.main_window import MainWindow

BOARD = "820-00239"


def live() -> np.ndarray:
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    frame[:] = (40, 120, 40)
    return frame


def good() -> np.ndarray:
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    frame[:] = (200, 200, 200)
    return frame


def test_off_or_no_reference_leaves_the_frame_alone() -> None:
    assert compare(live(), good(), ReferenceSettings(), 0.0) is not None
    assert np.array_equal(compare(live(), good(), ReferenceSettings(), 0.0), live())
    on = ReferenceSettings(mode=CompareMode.OVERLAY)
    assert np.array_equal(compare(live(), None, on, 0.0), live())


def test_overlay_fades_by_opacity() -> None:
    half = compare(live(), good(), ReferenceSettings(CompareMode.OVERLAY, 0.5), 0.0)
    assert tuple(half[45, 80]) == pytest.approx((120, 160, 120), abs=1)
    full = compare(live(), good(), ReferenceSettings(CompareMode.OVERLAY, 1.0), 0.0)
    assert np.array_equal(full, good())


def test_blink_alternates() -> None:
    blink = ReferenceSettings(CompareMode.BLINK)
    assert np.array_equal(compare(live(), good(), blink, 0.0), live())
    assert np.array_equal(compare(live(), good(), blink, BLINK_SECONDS + 0.01), good())


def test_a_different_size_or_grey_photo_is_fitted() -> None:
    photo = cv2.cvtColor(cv2.resize(good(), (400, 300)), cv2.COLOR_BGR2GRAY)
    out = compare(live(), photo, ReferenceSettings(CompareMode.OVERLAY, 1.0), 0.0)
    assert out.shape == live().shape and out[45, 80, 0] == 200


def test_store_keeps_one_reference_per_board(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.set_board_reference(BOARD, "/a.png")
    store.set_board_reference(BOARD, "/b.png")  # replaced, not duplicated
    assert store.board_reference(BOARD) == "/b.png"
    assert store.board_reference("nope") is None
    assert store.references_dir().is_dir()
    store.close()


@pytest.fixture
def window(qtbot, tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    window = MainWindow(Config(), job_store=store)
    qtbot.addWidget(window)
    return window, store


def _select(window, store, board_id) -> int:
    job = store.create_job("board", board_id=board_id).id
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job)
    return job


def test_capture_saves_per_board_and_reloads(window) -> None:
    window, store = window
    _select(window, store, BOARD)
    assert "No reference for 820-00239" in window.controls_panel.reference_status.text()
    window.scope_panel.update_frame(good())
    window.capture_reference()
    path = store.board_reference(BOARD)
    assert path and cv2.imread(path) is not None
    assert "Saved as the known-good reference" in window.controls_panel.reference_status.text()
    # Another job on the same board picks it up.
    window._reference = None
    _select(window, store, BOARD)
    assert (
        window._reference is not None and "loaded" in window.controls_panel.reference_status.text()
    )


def test_capture_needs_a_board_number(window) -> None:
    window, store = window
    _select(window, store, None)
    window.scope_panel.update_frame(good())
    window.capture_reference()
    assert store.references_dir() is not None
    assert "board number" in window.controls_panel.reference_status.text()


def test_load_a_photo(window, tmp_path) -> None:
    window, store = window
    _select(window, store, BOARD)
    photo = tmp_path / "good.jpg"
    cv2.imwrite(str(photo), good())
    window.choose_reference_file = lambda: str(photo)
    window.load_reference()
    assert store.board_reference(BOARD).endswith("820-00239.png")
    window.choose_reference_file = lambda: str(tmp_path / "missing.png")
    window.load_reference()
    assert "Couldn't read" in window.controls_panel.reference_status.text()


def test_the_composite_shows_the_reference_when_on(window) -> None:
    window, store = window
    _select(window, store, BOARD)
    window.scope_panel.update_frame(good())
    window.capture_reference()
    window.scope_panel.update_frame(live())
    panel = window.controls_panel
    panel.reference_mode.setCurrentIndex(panel.reference_mode.findData("overlay"))
    panel.reference_opacity.setValue(100)
    shown = []
    window._show_composite = shown.append
    window.render_composite()
    assert shown and shown[-1][45, 80, 0] == 200
