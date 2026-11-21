"""Which scene a tool's window belongs on, and when to go back (#455).

Qt-free: the rules here are the part worth getting right, so they're kept away
from sockets and timers. A tool that names a scene gets that scene shown when it
starts. When it exits the scene goes back to what was showing before — but only
if the tool's scene is still the live one. If someone changed scenes by hand in
the meantime, that choice stands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class _Running:
    scene: str
    back_to: str | None
    process: Any


class ToolScenes:
    """Decides the scene changes for tools as they start and exit.

    ``switch`` asks OBS for a scene; ``current`` is the live scene's name, or
    None when it isn't known (OBS not connected).
    """

    def __init__(self, switch: Callable[[str], Any], current: Callable[[], str | None]) -> None:
        self._switch = switch
        self._current = current
        self._running: list[_Running] = []

    @property
    def watching(self) -> bool:
        """Whether any started tool is still being waited on."""
        return bool(self._running)

    def tool_started(self, scene: str, process: Any = None) -> None:
        """A tool whose scene is ``scene`` was started as ``process``.

        ``process`` needs a ``poll()`` that returns None while it runs. Without
        one (a command typed into the terminal) the scene is shown and nothing
        brings it back.
        """
        if not scene:
            return
        showing = self._current()
        if showing != scene:
            self._switch(scene)
        if process is None:
            return
        # Starting a second tool on the same scene mustn't remember that scene
        # as the place to go back to.
        back_to = showing if showing != scene else self._back_to_of(scene)
        self._running.append(_Running(scene, back_to, process))

    def _back_to_of(self, scene: str) -> str | None:
        for running in self._running:
            if running.scene == scene:
                return running.back_to
        return None

    def poll(self) -> None:
        """Notice the tools that have exited and put their scenes back."""
        still: list[_Running] = []
        ended: list[_Running] = []
        for running in self._running:
            (still if running.process.poll() is None else ended).append(running)
        self._running = still
        for running in ended:
            if any(r.scene == running.scene for r in still):
                continue  # another window of that tool is still up
            if running.back_to and self._current() == running.scene:
                self._switch(running.back_to)
