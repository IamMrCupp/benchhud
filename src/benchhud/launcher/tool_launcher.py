"""Launch external tools configured in ``[[tools]]``.

The command comes from the user's own config, so this just parses it (``shlex``)
and spawns it. The spawn function is injectable so tests never start real
processes. The default spawns detached-ish via ``subprocess.Popen`` (fire and
forget — the HUD doesn't manage the tool's lifetime).
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

from benchhud.config import ToolConfig

_log = logging.getLogger(__name__)

SpawnFn = Callable[[list[str]], Any]


class ToolLauncher:
    """Launches configured external tools by command line."""

    def __init__(self, spawn: SpawnFn | None = None) -> None:
        self._spawn = spawn or self._default_spawn

    @staticmethod
    def _default_spawn(argv: list[str]) -> Any:
        return subprocess.Popen(argv)

    def launch(self, tool: ToolConfig, args: Sequence[str] = ()) -> Any:
        """Parse ``tool.command`` and spawn it. Returns the spawn result.

        ``args`` are appended to the parsed command — used to open a specific
        file with a tool (e.g. a board view in OpenBoardView).
        """
        argv = shlex.split(tool.command)
        if not argv:
            raise ValueError(f"tool {tool.name!r} has an empty command")
        argv.extend(args)
        _log.info("launching tool %s: %s", tool.name, argv)
        return self._spawn(argv)
