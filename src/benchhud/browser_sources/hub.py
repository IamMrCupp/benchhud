"""The latest payload per topic, handed from the GUI thread to the server's.

The GUI thread publishes on every telemetry tick; each connected browser source
is a server thread blocked in :meth:`TopicHub.wait` until something newer
arrives. Only the latest value is kept: a page that falls behind skips to now
rather than replaying a backlog, which is what a live readout wants.
"""

from __future__ import annotations

import threading
from typing import Any


class TopicHub:
    """Thread-safe latest-value store with a version counter per topic."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._latest: dict[str, tuple[int, Any]] = {}
        self._wakes = 0

    def publish(self, topic: str, payload: Any) -> int:
        """Replace ``topic``'s payload and wake its readers. Returns the new version."""
        with self._cond:
            version = self._latest.get(topic, (0, None))[0] + 1
            self._latest[topic] = (version, payload)
            self._cond.notify_all()
            return version

    def latest(self, topic: str) -> tuple[int, Any]:
        """``(version, payload)``; version 0 means nothing published yet."""
        with self._cond:
            return self._latest.get(topic, (0, None))

    def wait(self, topic: str, after: int, timeout: float) -> tuple[int, Any]:
        """Block until ``topic`` is newer than version ``after``, or ``timeout``.

        Returns the latest ``(version, payload)`` either way; a caller sees a
        timeout as an unchanged version. :meth:`wake_all` also returns it early.
        """
        with self._cond:
            wakes = self._wakes
            self._cond.wait_for(
                lambda: self._wakes != wakes or self._latest.get(topic, (0, None))[0] > after,
                timeout=timeout,
            )
            return self._latest.get(topic, (0, None))

    def wake_all(self) -> None:
        """Return every current :meth:`wait` now, so a stopping server's threads exit.

        The hub itself stays usable: a restarted server serves the same topics.
        """
        with self._cond:
            self._wakes += 1
            self._cond.notify_all()
