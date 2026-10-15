"""A camera that falls back to the test pattern, keeps retrying, and says why.

The scope preview must never go blank: a camera that's unplugged, busy, or
not yet allowed by the OS falls back to :class:`SyntheticFrameSource`. The
fallback used to happen once, at startup, silently, and forever. That hid
three real situations:

- **macOS camera permission.** The first open happens while the OS prompt is
  still on screen, so it fails. Once the operator clicks Allow, nothing retried
  the camera, so benchhud needed a relaunch.
- **Late plug-in and unplug.** A camera that arrives after launch was never
  picked up. One pulled mid-session left the preview frozen on its last frame,
  because OpenCV keeps reporting "open" and returns no frames.
- **Nobody could tell.** The status bar showed the configured device either
  way, so "live" and "test pattern" looked the same.

:class:`FallbackFrameSource` wraps the camera. It retries opening on an interval,
treats a long run of missed frames as a lost camera, and publishes a
:class:`SourceStatus` that the shell shows in the status bar. It paces the
fallback itself: the synthetic source never blocks, so a reader thread polling
it unpaced spins a whole core.

With ``fallback=None`` there is no test pattern: ``read`` returns ``None``
while the camera is down. The thermal panel needs this, because a fake heatmap
with a temperature on it could be mistaken for a real reading. A camera can
also report a ``fatal_error``, a reason retrying won't fix (a webcam picked as
the thermal camera). That stops the retries, so a Mac's camera light doesn't
blink every few seconds.

Everything runs on the reader's thread, so a slow ``open()`` never stalls
the GUI. Clock and sleep are injected so tests control time.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from benchhud.capture.frame_source import FrameSource


class Camera(Protocol):
    """The slice of :class:`~benchhud.capture.camera.CameraFrameSource` we drive."""

    @property
    def is_open(self) -> bool: ...

    @property
    def size(self) -> tuple[int, int]: ...

    def open(self) -> bool: ...

    def read(self) -> np.ndarray | None: ...

    def release(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SourceStatus:
    """Whether the preview is showing the real camera, and if not, why."""

    live: bool
    reason: str = ""  # empty when live


class FallbackFrameSource:
    """A :class:`FrameSource` that prefers a camera and falls back honestly."""

    def __init__(
        self,
        camera: Camera | None,
        fallback: FrameSource | None,
        *,
        name: str = "camera",
        open_hint: str = "",
        retry_interval: float = 3.0,
        stall_timeout: float = 3.0,
        fallback_fps: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._camera = camera
        self._fallback = fallback
        self._name = name
        self._open_hint = open_hint
        self._retry_interval = retry_interval
        self._stall_timeout = stall_timeout
        self._frame_period = 1.0 / fallback_fps
        self._clock = clock
        self._sleep = sleep

        now = clock()
        self._next_retry = now + retry_interval
        self._last_frame_at = now
        if camera is None:
            self._status = SourceStatus(False, "no camera selected")
        elif camera.is_open:
            self._status = SourceStatus(False, f"waiting for {name}…")
        else:
            self._status = SourceStatus(False, self._open_failed())

    # --- state -----------------------------------------------------------

    @property
    def camera(self) -> Camera | None:
        """The real camera behind the fallback, or None (for its size note, #247)."""
        return self._camera

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def size(self) -> tuple[int, int]:
        if (self._status.live or self._fallback is None) and self._camera is not None:
            return self._camera.size
        return self._fallback.size if self._fallback is not None else (0, 0)

    def _fatal(self) -> str | None:
        return getattr(self._camera, "fatal_error", None)

    def _open_failed(self) -> str:
        fatal = self._fatal()
        if fatal:
            return f"{self._name} {fatal}"
        reason = f"couldn't open {self._name}"
        return f"{reason} — {self._open_hint}" if self._open_hint else reason

    # --- frames ----------------------------------------------------------

    def read(self) -> np.ndarray | None:
        camera = self._camera
        if camera is not None:
            now = self._clock()
            if not camera.is_open and now >= self._next_retry and not self._fatal():
                self._next_retry = now + self._retry_interval
                if camera.open():
                    self._last_frame_at = now
                else:
                    self._status = SourceStatus(False, self._open_failed())
            if camera.is_open:
                frame = camera.read()
                if frame is not None:
                    self._last_frame_at = now
                    self._status = SourceStatus(True)
                    return frame
                if self._fatal():
                    # The first frame proved it's the wrong kind of camera.
                    camera.release()
                    self._status = SourceStatus(False, self._open_failed())
                elif now - self._last_frame_at < self._stall_timeout:
                    # A dropped frame or the thermal NUC shutter: hold the last
                    # good frame rather than flashing the test pattern.
                    self._sleep(self._frame_period)
                    return None
                else:
                    # OpenCV keeps an unplugged camera "open" forever; let it go
                    # and start retrying.
                    camera.release()
                    self._next_retry = now + self._retry_interval
                    self._status = SourceStatus(False, f"{self._name} stopped sending frames")

        self._sleep(self._frame_period)
        return self._fallback.read() if self._fallback is not None else None

    def close(self) -> None:
        if self._camera is not None:
            self._camera.close()
        if self._fallback is not None:
            self._fallback.close()
