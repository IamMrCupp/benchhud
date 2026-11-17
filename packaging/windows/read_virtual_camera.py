"""Read the Windows virtual camera the way a camera app does, and say what came.

CI's ``msi-verify`` job runs this against the installed benchhud: the app is
started with output on, and this opens the "DirectShow Softcam" camera through
DirectShow and checks that a real picture arrives at the configured size. It
is the installed build's version of ``tests/test_softcam_windows.py``.

    python packaging/windows/read_virtual_camera.py 1280 720 [seconds] [frame.png]

Exits 0 when a camera delivers frames of that size that aren't a flat colour,
1 otherwise. With a fourth argument it saves the frame it accepted, and CI
keeps that as an artifact: "not flat" is a number, and the picture is the thing
to look at. Needs only ``opencv-python`` and ``numpy``, not benchhud.
"""

from __future__ import annotations

import sys
import time


def main(argv: list[str]) -> int:
    import cv2

    width, height = int(argv[0]), int(argv[1])
    seconds = float(argv[2]) if len(argv) > 2 else 60.0
    save_to = argv[3] if len(argv) > 3 else ""
    deadline = time.monotonic() + seconds
    # The app may still be starting, so keep going round the cameras.
    while time.monotonic() < deadline:
        for index in range(4):
            capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
            try:
                if not capture.isOpened():
                    continue
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                for _ in range(30):
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        time.sleep(0.05)
                        continue
                    if frame.shape[:2] != (height, width):
                        print(f"camera {index}: {frame.shape[1]}x{frame.shape[0]}, not the HUD")
                        break
                    spread = float(frame.std())
                    print(
                        f"camera {index}: {width}x{height}, mean {frame.mean():.1f}, "
                        f"spread {spread:.1f}"
                    )
                    # A flat frame is what a camera with no sender shows.
                    if spread > 5.0:
                        print("a picture is arriving from the virtual camera")
                        if save_to:
                            cv2.imwrite(save_to, frame)
                            print(f"saved it to {save_to}")
                        return 0
            finally:
                capture.release()
        time.sleep(1.0)
    print(f"no {width}x{height} picture from any DirectShow camera in {seconds:.0f} s")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
