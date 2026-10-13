"""Launch external tools configured in ``[[tools]]``.

The command comes from the user's own config, so this just parses it (``shlex``)
and spawns it. The spawn function is injectable so tests never start real
processes. The default spawns detached-ish via ``subprocess.Popen`` (fire and
forget — the HUD doesn't manage the tool's lifetime).

A command-line tool (``terminal = true``) normally opens in benchhud's own
Terminal panel; the main window does that. :meth:`ToolLauncher.launch_in_system_terminal`
is the fallback when the panel isn't available (it's off, or on Windows): the
tool opens in a terminal window of the operating system's own (#225).
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any

from benchhud.config import ToolConfig

_log = logging.getLogger(__name__)

SpawnFn = Callable[[list[str]], Any]

#: Linux terminals tried in order, with how each takes a command to run.
#: ``x-terminal-emulator`` is Debian/Ubuntu's alternatives link to the default.
_LINUX_TERMINALS = (
    ("x-terminal-emulator", "-e"),
    ("gnome-terminal", "--"),
    ("konsole", "-e"),
    ("xfce4-terminal", "-x"),
    ("kitty", ""),
    ("alacritty", "-e"),
    ("xterm", "-e"),
)

#: Windows: give the console its own window, so a CLI tool has somewhere to run.
CREATE_NEW_CONSOLE = 0x00000010


class NoTerminalError(RuntimeError):
    """No terminal window could be found to run a command-line tool in."""


def system_terminal_argv(
    command: str,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """argv that opens the OS's terminal running ``command``, then leaves a shell.

    The shell stays open after the tool exits, so its output (usage, errors)
    can be read and the next command typed.
    """
    if platform == "darwin":
        # Terminal.app's "do script" opens a window with a login shell and runs
        # the command in it; the shell remains afterwards.
        script = command.replace("\\", "\\\\").replace('"', '\\"')
        return [
            "osascript",
            "-e",
            f'tell application "Terminal" to do script "{script}"',
            "-e",
            'tell application "Terminal" to activate',
        ]
    if platform == "win32":
        return ["cmd.exe", "/k", command]
    for name, flag in _LINUX_TERMINALS:
        if which(name):
            keep_open = f'{command}; exec "${{SHELL:-sh}}"'
            run = ["sh", "-c", keep_open]
            return [name, flag, *run] if flag else [name, *run]
    raise NoTerminalError("no terminal emulator found (tried x-terminal-emulator, gnome-terminal…)")


class ToolLauncher:
    """Launches configured external tools by command line."""

    def __init__(self, spawn: SpawnFn | None = None) -> None:
        self._spawn = spawn or self._default_spawn

    @staticmethod
    def _default_spawn(argv: list[str]) -> Any:
        if sys.platform == "win32" and argv[:1] == ["cmd.exe"]:
            return subprocess.Popen(argv, creationflags=CREATE_NEW_CONSOLE)
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

    def launch_in_system_terminal(
        self,
        tool: ToolConfig,
        platform: str = sys.platform,
        which: Callable[[str], str | None] = shutil.which,
    ) -> Any:
        """Open a command-line tool in the OS's own terminal window.

        Raises :class:`NoTerminalError` when there's none to open (a Linux box
        with no terminal emulator installed).
        """
        if not tool.command.strip():
            raise ValueError(f"tool {tool.name!r} has an empty command")
        argv = system_terminal_argv(tool.command, platform, which)
        _log.info("launching tool %s in a terminal: %s", tool.name, argv)
        return self._spawn(argv)
