"""OpenCV's thread count, set once at startup (#415).

OpenCV spreads a resize or a blend over a pool of worker threads, one per core
by default. On the bench that pool cost about a core more than two threads did,
for the same output frame rate: the work is small, and at a 15 fps cap a frame
has 66 ms to spare. ``performance.opencv_threads`` picks the count.
"""

from __future__ import annotations

import logging

import cv2

log = logging.getLogger(__name__)


def apply(count: int) -> int:
    """Limit OpenCV to ``count`` threads and return the count now in force.

    Zero or less leaves OpenCV's own default (and ``OPENCV_FOR_THREADS_NUM``,
    if the environment sets it) alone. What comes back is what OpenCV reports,
    which isn't always what was asked for: a build on GCD (the macOS wheel)
    ignores the count and keeps one thread per core.
    """
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        cv2.setNumThreads(count)
    in_force = int(cv2.getNumThreads())
    log.info("OpenCV threads: %d", in_force)
    return in_force
