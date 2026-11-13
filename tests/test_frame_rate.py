"""The frame loop runs at ``output.fps`` while the output is on (#359)."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.config import Config, OutputConfig
from benchhud.shell.main_window import MainWindow, frame_interval_ms


class _Still:
    """A frame source that hands back the same frame forever."""

    def read(self) -> np.ndarray:
        return np.zeros((4, 4, 3), dtype=np.uint8)

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("enabled", "fps", "expected"),
    [
        (True, 15, 67),
        (True, 20, 50),
        (True, 30, 33),
        (True, 60, 17),
        (False, 15, 33),  # output off: the preview keeps its own 30
        (True, 500, 17),  # capped at 60
        (True, 0, 1000),  # a zero in the file must not divide by zero
        (True, -5, 1000),
    ],
)
def test_interval_follows_the_output_rate(enabled: bool, fps: int, expected: int) -> None:
    assert frame_interval_ms(OutputConfig(enabled=enabled, fps=fps)) == expected


def _window(qtbot, *, enabled: bool, fps: int) -> MainWindow:
    config = Config()
    config.output.enabled = enabled
    config.output.fps = fps
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.start_preview(_Still)
    window.start_thermal(_Still)
    return window


def test_both_timers_start_at_the_output_rate(qtbot) -> None:
    window = _window(qtbot, enabled=True, fps=20)
    try:
        assert window._timer.interval() == 50
        assert window._thermal_timer.interval() == 50
    finally:
        window.close()


def test_preview_runs_at_30_with_the_output_off(qtbot) -> None:
    window = _window(qtbot, enabled=False, fps=5)
    try:
        assert window._timer.interval() == 33
    finally:
        window.close()


def test_a_new_rate_applies_without_a_restart(qtbot) -> None:
    window = _window(qtbot, enabled=True, fps=30)
    try:
        window._config.output.fps = 15
        window.reconfigure_frame_rate()
        assert window._timer.interval() == 67
        assert window._thermal_timer.interval() == 67
        assert window._timer.isActive()
    finally:
        window.close()


def test_repacing_with_nothing_running_is_a_no_op(qtbot) -> None:
    window = MainWindow(Config())
    qtbot.addWidget(window)
    window.reconfigure_frame_rate()
    window.close()
