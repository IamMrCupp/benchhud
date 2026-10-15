"""The resolutions a camera offers, for the scope's Resolution picker (#247).

On Linux the camera says, through ``v4l2-ctl --list-formats-ext`` (from
v4l2-utils, already part of the bench setup). Elsewhere, or without
v4l2-ctl, it's a short list of common sizes, and the camera picks the
nearest it can do.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable

#: Offered when the camera can't be asked.
COMMON_SIZES: tuple[tuple[int, int], ...] = (
    (3840, 2160),
    (2560, 1440),
    (1920, 1080),
    (1280, 720),
    (640, 480),
)

_FORMAT = re.compile(r"\[\d+\]: '(\w+)'")
_SIZE = re.compile(r"Size: Discrete (\d+)x(\d+)")


def parse_formats(output: str) -> list[tuple[int, int]]:
    """Sizes from ``v4l2-ctl --list-formats-ext``, preferring MJPG's, largest first.

    MJPG is what reaches 1080p and 4K over USB 2; raw YUYV usually stops far
    lower. If the camera has no MJPG, every size is offered.
    """
    by_format: dict[str, set[tuple[int, int]]] = {}
    current = ""
    for line in output.splitlines():
        found = _FORMAT.search(line)
        if found:
            current = found.group(1)
            continue
        size = _SIZE.search(line)
        if size and current:
            by_format.setdefault(current, set()).add((int(size.group(1)), int(size.group(2))))
    if not by_format:
        return []
    sizes = by_format.get("MJPG") or set().union(*by_format.values())
    return sorted(sizes, key=lambda s: (s[0] * s[1], s[0]), reverse=True)


def camera_modes(
    device: str,
    *,
    platform: str = sys.platform,
    run: Callable[[list[str]], str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[tuple[int, int]]:
    """The sizes to offer for ``device``."""
    if platform.startswith("linux") and device.startswith("/dev/") and which("v4l2-ctl"):
        try:
            output = (run or _run)(["v4l2-ctl", "-d", device, "--list-formats-ext"])
        except (OSError, subprocess.SubprocessError):
            output = ""
        sizes = parse_formats(output)
        if sizes:
            return sizes
    return list(COMMON_SIZES)


def _run(argv: list[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False).stdout


def describe(size: tuple[int, int]) -> str:
    width, height = size
    if width == 0 or height == 0:
        return "Camera default"
    label = f"{width} x {height}"
    if width * height >= 3840 * 2160:
        label += " (4K, heavier on the CPU)"
    return label
