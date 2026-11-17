"""performance.opencv_threads reaches OpenCV (#415).

The count is checked through the call, not through ``cv2.getNumThreads()``:
OpenCV built on GCD (the macOS wheel) ignores the setting and always reports
one thread per core.
"""

from __future__ import annotations

import pytest

from benchhud import config as cfg
from benchhud import cvthreads


@pytest.fixture
def calls(monkeypatch) -> list[int]:
    seen: list[int] = []
    monkeypatch.setattr(cvthreads.cv2, "setNumThreads", seen.append)
    return seen


def test_a_count_is_passed_to_opencv(calls) -> None:
    in_force = cvthreads.apply(2)

    assert calls == [2]
    assert in_force == cvthreads.cv2.getNumThreads()


@pytest.mark.parametrize("count", [0, -1, True, "2", None])
def test_zero_or_junk_leaves_opencv_alone(calls, count) -> None:
    cvthreads.apply(count)

    assert calls == []


def test_the_default_is_two_and_the_file_overrides_it() -> None:
    assert cfg.Config().performance.opencv_threads == 2

    loaded = cfg.Config.from_dict({"performance": {"opencv_threads": 4, "bogus": 1}})

    assert loaded.performance.opencv_threads == 4
