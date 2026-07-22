#!/usr/bin/env python3
"""Verify CameraFrameSource against a real camera.

macOS gates camera access per-app and only grants it to a process it can
attribute to an app bundle — so run this from Terminal.app (or iTerm), which can
show the permission prompt and will then appear in Privacy & Security → Camera.

    uv run scripts/check_camera.py [device]

``device`` is a camera index (``0``) or a path (``/dev/video4``); default ``0``.
Linux has no such gate, so there this is simply a quick smoke test of a device —
handy for confirming which ``/dev/videoN`` is the scope and which is the thermal.
"""

from __future__ import annotations

import sys

import numpy as np

from benchhud.capture.camera import CameraFrameSource

OUT = "camera-check.png"
FRAMES = 10


def main() -> int:
    device = sys.argv[1] if len(sys.argv) > 1 else "0"
    source = CameraFrameSource(device)
    print(f"device={source.device!r} open={source.is_open} size={source.size}")
    if not source.is_open:
        print(f"FAILED: {source.last_error}")
        print("On macOS: grant this terminal camera access, then re-run.")
        return 1

    frames = [f for f in (source.read() for _ in range(FRAMES)) if f is not None]
    print(f"grabbed {len(frames)}/{FRAMES} frames")
    if not frames:
        print(f"FAILED: opened but delivered no frames ({source.last_error})")
        source.close()
        return 1

    first, last = frames[0], frames[-1]
    print(f"shape={last.shape} dtype={last.dtype} mean={last.mean():.1f}")
    # Identical first/last frames means a frozen or dummy feed, not a live one.
    print(f"live (frames differ): {not np.array_equal(first, last)}")

    import cv2

    cv2.imwrite(OUT, last)
    print(f"wrote {OUT}")

    source.close()
    print(f"closed: open={source.is_open}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
