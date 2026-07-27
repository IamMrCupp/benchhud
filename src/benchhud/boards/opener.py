"""Open a board-view file in a boardview viewer (OpenBoardView or FlexBV).

benchhud doesn't render board views — it locates the file and hands it to the
viewer. The command comes from the operator's existing **Tools launcher** entry
(they already configure it there), with the board file appended as an argument,
so there's no second place to configure the same binary.

Two viewers are recognised: **OpenBoardView** (open source) and **FlexBV** (its
Professional edition adds schematic cross-probe). Which one opens a board is a
config choice (``boards.viewer``): ``auto`` uses whichever is configured, or a
specific one can be pinned. Neither is bundled — benchhud only launches the
copy the operator installed.

FlexBV auto-loads a schematic PDF that shares the board file's name in the same
folder, so simply opening the board is enough to get the schematic — benchhud
keeps the two co-located in the library (see :mod:`benchhud.boards.library`).

If no known viewer is configured, fall back to the OS file handler. Both the
launcher and the OS opener are injectable, so tests never spawn a process.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from benchhud.config import ToolConfig
from benchhud.launcher.tool_launcher import ToolLauncher

log = logging.getLogger(__name__)

OsOpener = Callable[[Path], object]

#: Boardview viewers benchhud can hand a board to, by normalised name/command token.
BOARD_VIEWERS: tuple[str, ...] = ("openboardview", "flexbv")


def _normalise(text: str) -> str:
    """Lowercase, alphanumerics only — so "Open Board View" matches the token."""
    return "".join(char for char in text.lower() if char.isalnum())


def _first_matching(tools: Sequence[ToolConfig], tokens: Sequence[str]) -> ToolConfig | None:
    for tool in tools:
        name, command = _normalise(tool.name), _normalise(tool.command)
        if any(token in name or token in command for token in tokens):
            return tool
    return None


def find_board_viewer(tools: Sequence[ToolConfig], prefer: str = "auto") -> ToolConfig | None:
    """The configured board viewer to use.

    ``prefer`` is ``auto`` (any known viewer, in the order tools are listed) or a
    specific viewer token (``openboardview`` / ``flexbv``) to pin that one.
    """
    prefer_token = _normalise(prefer)
    if prefer_token in BOARD_VIEWERS:
        return _first_matching(tools, (prefer_token,))
    return _first_matching(tools, BOARD_VIEWERS)


def _default_os_open(path: Path) -> object:
    if sys.platform == "darwin":
        return subprocess.Popen(["open", str(path)])
    if sys.platform.startswith("win"):
        return subprocess.Popen(["cmd", "/c", "start", "", str(path)], shell=False)
    return subprocess.Popen(["xdg-open", str(path)])


def open_board(
    path: Path,
    tools: Sequence[ToolConfig],
    *,
    prefer: str = "auto",
    launcher: ToolLauncher | None = None,
    os_open: OsOpener | None = None,
) -> bool:
    """Open ``path`` in the configured board viewer (or the OS handler).

    A missing file or a viewer that won't start is logged, not raised — the HUD
    must survive a bad board file.
    """
    if not path.exists():
        log.warning("board file missing: %s", path)
        return False

    viewer = find_board_viewer(tools, prefer)
    try:
        if viewer is not None:
            (launcher or ToolLauncher()).launch(viewer, [str(path)])
        else:
            log.info("no board viewer configured; using the OS handler for %s", path)
            (os_open or _default_os_open)(path)
    except Exception:
        log.exception("could not open board file %s", path)
        return False
    return True


def open_schematic(path: Path, *, os_open: OsOpener | None = None) -> bool:
    """Open a schematic PDF directly via the OS handler (a quick standalone look).

    Opening the *board* in FlexBV already pulls in the matching schematic; this
    is for glancing at the PDF on its own — e.g. when the viewer is OBV, which
    has no schematic view. Never raises into the HUD.
    """
    if not path.exists():
        log.warning("schematic file missing: %s", path)
        return False
    try:
        (os_open or _default_os_open)(path)
    except Exception:
        log.exception("could not open schematic %s", path)
        return False
    return True
