"""Open a board-view file in OpenBoardView.

benchhud doesn't render board views — it locates the file and hands it to OBV.
The command comes from the operator's existing **Tools launcher** entry (they
already configure it there), with the board file appended as an argument, so
there's no second place to configure the same binary.

If no OpenBoardView tool is configured, fall back to the OS file handler. That
degrades sensibly rather than doing nothing, though it gives up the guarantee
that OBV specifically opens the file.

FlexBV deliberately does **not** match — the handoff specifies OBV.

Both the launcher and the OS opener are injectable, so tests never spawn a
process.
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

_VIEWER_TOKEN = "openboardview"


def _normalise(text: str) -> str:
    """Lowercase, alphanumerics only — so "Open Board View" matches the token."""
    return "".join(char for char in text.lower() if char.isalnum())


def find_board_viewer(tools: Sequence[ToolConfig]) -> ToolConfig | None:
    """The configured OpenBoardView tool, if the operator has one."""
    for tool in tools:
        if _VIEWER_TOKEN in _normalise(tool.name) or _VIEWER_TOKEN in _normalise(tool.command):
            return tool
    return None


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
    launcher: ToolLauncher | None = None,
    os_open: OsOpener | None = None,
) -> bool:
    """Open ``path`` in OpenBoardView (or the OS handler). False if it failed.

    A missing file or a viewer that won't start is logged, not raised — the HUD
    must survive a bad board file.
    """
    if not path.exists():
        log.warning("board file missing: %s", path)
        return False

    viewer = find_board_viewer(tools)
    try:
        if viewer is not None:
            (launcher or ToolLauncher()).launch(viewer, [str(path)])
        else:
            log.info("no OpenBoardView tool configured; using the OS handler for %s", path)
            (os_open or _default_os_open)(path)
    except Exception:
        log.exception("could not open board file %s", path)
        return False
    return True
