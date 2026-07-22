"""Catalog of common bench tools, and detection of what's installed.

Rather than making people hand-type a command, benchhud ships a curated list of
tools a board-repair bench typically uses and looks for them on the system. What
it finds can be enabled in one click; anything missing is shown as not-found (so
the list doubles as a hint of what's out there).

Detection is deliberately conservative: an executable on ``PATH`` (via
``shutil.which``), or a macOS ``.app`` bundle. Both lookups are injectable so
tests never depend on what happens to be installed on the machine.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnownTool:
    """A tool benchhud knows how to look for."""

    name: str
    category: str
    description: str
    # Candidate executable names, most-preferred first.
    commands: tuple[str, ...] = ()
    # macOS application bundle name (without ".app"), if it ships as one.
    macos_app: str | None = None


@dataclass(frozen=True)
class DetectedTool:
    """A catalog entry plus where (if anywhere) it was found."""

    tool: KnownTool
    command: str | None = None  # what to run
    path: str | None = None  # where it was found (for display)

    @property
    def found(self) -> bool:
        return self.command is not None


KNOWN_TOOLS: tuple[KnownTool, ...] = (
    KnownTool(
        "OpenBoardView",
        "Boardview",
        "Boardview viewer — locate nets and components on a board",
        ("openboardview",),
        macos_app="OpenBoardView",
    ),
    KnownTool("FlexBV", "Boardview", "Boardview viewer", ("flexbv",), macos_app="FlexBV"),
    KnownTool(
        "minipro GUI",
        "Programmer",
        "GUI for the minipro / TL866 chip programmer",
        ("minipro-gui", "miniprogui"),
    ),
    KnownTool("minipro", "Programmer", "minipro / TL866 CLI programmer", ("minipro",)),
    KnownTool("flashrom", "Programmer", "Read/write SPI and BIOS flash", ("flashrom",)),
    KnownTool("avrdude", "Programmer", "AVR programmer", ("avrdude",)),
    KnownTool(
        "DSView",
        "Logic analyzer",
        "DSLogic capture and protocol decoding",
        ("DSView", "dsview"),
        macos_app="DSView",
    ),
    KnownTool(
        "PulseView",
        "Logic analyzer",
        "sigrok logic analyzer front-end",
        ("pulseview",),
        macos_app="PulseView",
    ),
    KnownTool("sigrok-cli", "Logic analyzer", "sigrok command line", ("sigrok-cli",)),
    KnownTool("OpenOCD", "Debug / JTAG", "On-chip debugger (JTAG/SWD)", ("openocd",)),
    KnownTool("picocom", "Serial terminal", "Minimal serial terminal", ("picocom",)),
    KnownTool("minicom", "Serial terminal", "Serial terminal", ("minicom",)),
    KnownTool("GtkTerm", "Serial terminal", "GTK serial terminal", ("gtkterm",)),
    KnownTool("KiCad", "EDA", "Schematic and PCB design", ("kicad",), macos_app="KiCad"),
    KnownTool(
        "qv4l2",
        "Camera",
        "V4L2 device control — handy for checking camera formats",
        ("qv4l2", "v4l2-ctl"),
    ),
)


def _macos_app_exists(app: str) -> bool:
    return Path(f"/Applications/{app}.app").exists()


def detect_tool(
    tool: KnownTool,
    *,
    which: Callable[[str], str | None] = shutil.which,
    app_exists: Callable[[str], bool] = _macos_app_exists,
    platform: str = sys.platform,
) -> DetectedTool:
    """Look for one catalog entry on this system."""
    for command in tool.commands:
        found = which(command)
        if found:
            # Store the plain command (portable); show where it resolved.
            return DetectedTool(tool, command=command, path=found)

    if platform == "darwin" and tool.macos_app and app_exists(tool.macos_app):
        bundle = f"/Applications/{tool.macos_app}.app"
        return DetectedTool(tool, command=f'open -a "{tool.macos_app}"', path=bundle)

    return DetectedTool(tool)


def detect_tools(
    tools: tuple[KnownTool, ...] = KNOWN_TOOLS,
    *,
    which: Callable[[str], str | None] = shutil.which,
    app_exists: Callable[[str], bool] = _macos_app_exists,
    platform: str = sys.platform,
) -> list[DetectedTool]:
    """Scan the catalog; found tools first, then the rest, each grouped by name."""
    results = [
        detect_tool(tool, which=which, app_exists=app_exists, platform=platform) for tool in tools
    ]
    return sorted(results, key=lambda d: (not d.found, d.tool.category, d.tool.name))


def categories(tools: tuple[KnownTool, ...] = KNOWN_TOOLS) -> list[str]:
    """Distinct categories, in catalog order."""
    seen: list[str] = []
    for tool in tools:
        if tool.category not in seen:
            seen.append(tool.category)
    return seen
