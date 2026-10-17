"""Whether Linux's camera driver hands torn frames to applications (#297).

When pieces of a frame go missing on USB, ``uvcvideo`` either drops the frame
or passes it on incomplete, depending on its ``nodrop`` parameter. Passed on,
an uncompressed thermal frame shows a band of rows shifted out of place: the
tearing seen on the reference bench, whose kernel defaults to ``nodrop=1``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

NODROP_PARAM = Path("/sys/module/uvcvideo/parameters/nodrop")

#: What the status bar says when torn frames get through.
TORN_FRAMES_WARNING = "thermal may tear: set uvcvideo nodrop=0 (see BENCH-SETUP)"


def _read_param() -> str:
    return NODROP_PARAM.read_text()


def passes_torn_frames(read: Callable[[], str] = _read_param, platform: str = sys.platform) -> bool:
    """True when the driver is known to pass incomplete frames on.

    Anything unknown (not Linux, the module not loaded, an unreadable value)
    is False: only warn about what's certain.
    """
    if not platform.startswith("linux"):
        return False
    try:
        value = read().strip()
    except OSError:
        return False
    return value in {"1", "Y", "y"}
