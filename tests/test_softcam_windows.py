"""The Windows virtual camera against the real softcam DLL.

``test_output_softcam.py`` pins the contract with a fake camera, on any OS.
This file is the other half: a second process publishes a known picture
through ``open_output_sink``, and the test reads it back as a camera app
would, through DirectShow. It checks the picture, not a frame count: a count
passed on macOS for weeks while every frame was being dropped (#178).

It needs Windows and a softcam.dll, so it runs in CI's ``softcam`` job, which
builds the DLL, runs the unregistered half, registers, then runs the rest.
``BENCHHUD_REQUIRE_SOFTCAM`` (``registered`` or ``unregistered``) turns the
skips for the wrong state into failures there, so a job that set nothing up
can't pass by skipping everything.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from benchhud.output import softcam_register
from benchhud.output.sink import NullSink, open_output_sink
from benchhud.output.softcam import (
    SOFTCAM_CAMERA_BUSY,
    SOFTCAM_NOT_REGISTERED,
    SoftcamUnavailable,
    open_softcam_sink,
)
from conftest import subprocess_env

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="softcam is the Windows backend")

#: The size benchhud publishes by default.
WIDTH, HEIGHT = 1280, 720

REQUIRED_STATE = os.environ.get("BENCHHUD_REQUIRE_SOFTCAM", "")

#: Publishes the picture in ``argv[1]`` until the file ``argv[2]`` appears.
SENDER = """
import os, sys, time
import numpy as np
from benchhud.output.sink import NullSink, open_output_sink

frame = np.load(sys.argv[1])
height, width = frame.shape[:2]
sink = open_output_sink("", width, height)
if isinstance(sink, NullSink):
    print("UNAVAILABLE " + sink.reason, flush=True)
    sys.exit(3)
print("READY", flush=True)
watched = False
deadline = time.monotonic() + 120
while not os.path.exists(sys.argv[2]) and time.monotonic() < deadline:
    sink.send(frame)
    if not watched and sink.connected:
        watched = True
        print("CONNECTED", flush=True)
    time.sleep(1 / 15)
sink.close()
print("DONE", flush=True)
"""


def _need(state: str) -> None:
    """Skip unless softcam is in ``state``; fail instead where CI demands it."""
    registered = softcam_register.status().ready
    if registered == (state == "registered"):
        return
    message = f"softcam is {'registered' if registered else 'not registered'}; needs {state}"
    if state == REQUIRED_STATE:
        pytest.fail(message)
    pytest.skip(message)


def _picture() -> np.ndarray:
    """Four flat quadrants, one colour each, so a flip or a channel swap shows.

    BGR, as OpenCV and benchhud hold frames: blue top left, green top right,
    red bottom left, white bottom right.
    """
    frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    half_h, half_w = HEIGHT // 2, WIDTH // 2
    frame[:half_h, :half_w] = (255, 0, 0)
    frame[:half_h, half_w:] = (0, 255, 0)
    frame[half_h:, :half_w] = (0, 0, 255)
    frame[half_h:, half_w:] = (255, 255, 255)
    return frame


def _corners(frame: np.ndarray) -> dict[str, tuple[int, ...]]:
    """The pixel in the middle of each quadrant, for a readable failure."""
    h, w = frame.shape[:2]
    points = {
        "top left": (h // 4, w // 4),
        "top right": (h // 4, 3 * w // 4),
        "bottom left": (3 * h // 4, w // 4),
        "bottom right": (3 * h // 4, 3 * w // 4),
    }
    return {name: tuple(int(v) for v in frame[y, x]) for name, (y, x) in points.items()}


class _Sender:
    """The publishing process, with its status lines read off a thread."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.lines: queue.Queue[str] = queue.Queue()
        self.seen: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.strip())

    def wait_for(self, word: str, timeout: float) -> bool:
        """Whether a line starting with ``word`` arrives within ``timeout`` seconds."""
        if any(line.startswith(word) for line in self.seen):
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                if self.process.poll() is not None and self.lines.empty():
                    return False
                continue
            self.seen.append(line)
            if line.startswith(word):
                return True
        return False


@pytest.fixture(scope="module")
def sender(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Sender]:
    """A second process publishing :func:`_picture` to the virtual camera."""
    _need("registered")
    folder = tmp_path_factory.mktemp("softcam")
    picture, stop = folder / "picture.npy", folder / "stop"
    np.save(picture, _picture())
    process = subprocess.Popen(
        [sys.executable, "-c", SENDER, str(picture), str(stop)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=subprocess_env(),  # the benchhud under test, not an installed one (#465)
    )
    running = _Sender(process)
    try:
        if not running.wait_for("READY", timeout=30):
            pytest.fail(f"the sender never opened the camera: {running.seen}")
        yield running
    finally:
        stop.touch()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def _read_matching_frame(expected: np.ndarray, seconds: float) -> tuple[np.ndarray | None, str]:
    """Read cameras through DirectShow until one shows ``expected``.

    OpenCV numbers DirectShow cameras without naming them, so each of the
    first few is tried: on a CI runner softcam is the only one, on a desk it
    sits beside a webcam. Returns the closest frame seen and what was tried.
    """
    import cv2

    tried: list[str] = []
    closest: np.ndarray | None = None
    deadline = time.monotonic() + seconds
    for index in range(4):
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            if not capture.isOpened():
                tried.append(f"camera {index}: did not open")
                continue
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, expected.shape[1])
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, expected.shape[0])
            frames = 0
            while time.monotonic() < deadline and frames < 60:
                ok, frame = capture.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue
                frames += 1
                if frame.shape == expected.shape:
                    closest = frame
                    if np.array_equal(frame, expected):
                        tried.append(f"camera {index}: matched on frame {frames}")
                        return frame, "; ".join(tried)
            shape = None if closest is None else closest.shape
            tried.append(f"camera {index}: {frames} frames, none matched (last shape {shape})")
        finally:
            capture.release()
    return closest, "; ".join(tried)


def test_an_unregistered_camera_is_reported_not_published_to() -> None:
    """Sending works without the registration, and no app could see it.

    So the sink must refuse and say how to register, not take frames quietly.
    """
    _need("unregistered")
    sink = open_output_sink("", WIDTH, HEIGHT)
    assert isinstance(sink, NullSink)
    assert sink.reason == SOFTCAM_NOT_REGISTERED


def test_status_finds_the_registered_dll() -> None:
    _need("registered")
    current = softcam_register.status()
    assert current.ready
    assert current.registered is not None
    assert current.registered.name.lower() == "softcam.dll"


def test_a_camera_app_sees_the_picture_unchanged(sender: _Sender) -> None:
    """The frame a DirectShow reader gets is the frame benchhud sent.

    Byte for byte: softcam's output is uncompressed RGB24, so the right way up
    and the right colour order are both exact, not approximate.
    """
    expected = _picture()
    frame, tried = _read_matching_frame(expected, seconds=45)
    assert frame is not None, f"no {WIDTH}x{HEIGHT} frame from any camera ({tried})"
    assert np.array_equal(frame, expected), (
        f"the picture changed on the way through: sent {_corners(expected)}, "
        f"read {_corners(frame)} ({tried})"
    )


def test_the_sender_learns_that_an_app_is_watching(sender: _Sender) -> None:
    """``scIsConnected`` turns true once a camera app has opened the camera."""
    # The picture test above may have been deselected, so open it here too.
    _read_matching_frame(_picture(), seconds=20)
    assert sender.wait_for("CONNECTED", timeout=10), f"sender said only: {sender.seen}"


def test_a_second_sender_is_refused_with_the_busy_reason(sender: _Sender) -> None:
    """softcam takes one sender system-wide; the second gets the real reason."""
    with pytest.raises(SoftcamUnavailable) as caught:
        open_softcam_sink(WIDTH, HEIGHT)
    assert caught.value.reason.startswith(SOFTCAM_CAMERA_BUSY)


def test_the_bundled_dll_is_the_one_the_build_script_left() -> None:
    """A checkout finds the DLL where ``build-softcam.ps1`` puts it."""
    from benchhud.output.softcam import bundled_dll_path

    built = Path(__file__).resolve().parents[1] / "packaging" / "windows" / "softcam"
    if not (built / "softcam.dll").is_file():
        pytest.skip("no softcam.dll built in this checkout")
    assert bundled_dll_path() == built / "softcam.dll"
