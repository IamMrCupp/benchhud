"""Catalog of common bench tools, and detection of what's installed.

Rather than making people hand-type a command, benchhud ships a curated list of
tools a board-repair bench typically uses and looks for them on the system. What
it finds can be enabled in one click; anything missing is shown as not-found (so
the list doubles as a hint of what's out there).

Detection runs on all three platforms. In order of preference:

* an executable on ``PATH`` (``shutil.which`` — finds ``.exe`` on Windows too);
* a macOS ``.app`` bundle in ``/Applications`` or ``~/Applications``;
* a Windows install under ``Program Files`` / ``Program Files (x86)``.

Every lookup is injectable so tests never depend on what happens to be installed
on the machine running them — and so Windows/macOS paths are testable from Linux.

Many tools are platform-specific by nature (``qv4l2`` needs V4L2; GTK terminals
are Linux-only). Each entry declares which platforms it applies to, so a Windows
bench isn't shown a wall of Linux-only tools it can never install.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# sys.platform values, named so the catalog reads clearly.
LINUX = "linux"
MACOS = "darwin"
WINDOWS = "win32"
ALL_PLATFORMS = (LINUX, MACOS, WINDOWS)


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
    # Windows: (subpath under Program Files, exe to run). The subpath is joined
    # under both "Program Files" and "Program Files (x86)".
    windows_install: tuple[str, str] | None = None
    # Which platforms this tool can exist on. Defaults to all three.
    platforms: tuple[str, ...] = ALL_PLATFORMS


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
        windows_install=("OpenBoardView", "OpenBoardView.exe"),
    ),
    KnownTool(
        "FlexBV",
        "Boardview",
        "Boardview viewer",
        ("flexbv",),
        macos_app="FlexBV",
        windows_install=("FlexBV", "FlexBV.exe"),
        platforms=(MACOS, WINDOWS),  # no Linux build
    ),
    KnownTool(
        "minipro GUI",
        "Programmer",
        "GUI for the minipro / TL866 chip programmer",
        ("minipro-gui", "miniprogui"),
        platforms=(LINUX,),
    ),
    KnownTool(
        "minipro",
        "Programmer",
        "minipro / TL866 CLI programmer",
        ("minipro",),
        platforms=(LINUX, MACOS),
    ),
    KnownTool(
        "Xgpro",
        "Programmer",
        "Vendor GUI for the TL866 / T48 programmer",
        (),
        windows_install=("Xgpro", "Xgpro.exe"),
        platforms=(WINDOWS,),
    ),
    KnownTool("flashrom", "Programmer", "Read/write SPI and BIOS flash", ("flashrom",)),
    KnownTool("avrdude", "Programmer", "AVR programmer", ("avrdude",)),
    KnownTool(
        "DSView",
        "Logic analyzer",
        "DSLogic capture and protocol decoding",
        ("DSView", "dsview"),
        macos_app="DSView",
        windows_install=("DSView", "DSView.exe"),
    ),
    KnownTool(
        "PulseView",
        "Logic analyzer",
        "sigrok logic analyzer front-end",
        ("pulseview",),
        macos_app="PulseView",
        windows_install=("sigrok-PulseView", "pulseview.exe"),
    ),
    KnownTool("sigrok-cli", "Logic analyzer", "sigrok command line", ("sigrok-cli",)),
    KnownTool("OpenOCD", "Debug / JTAG", "On-chip debugger (JTAG/SWD)", ("openocd",)),
    KnownTool(
        "picocom",
        "Serial terminal",
        "Minimal serial terminal",
        ("picocom",),
        platforms=(LINUX, MACOS),
    ),
    KnownTool(
        "minicom",
        "Serial terminal",
        "Serial terminal",
        ("minicom",),
        platforms=(LINUX, MACOS),
    ),
    KnownTool(
        "GtkTerm",
        "Serial terminal",
        "GTK serial terminal",
        ("gtkterm",),
        platforms=(LINUX,),
    ),
    KnownTool(
        "PuTTY",
        "Serial terminal",
        "Serial + SSH terminal",
        ("putty",),
        windows_install=("PuTTY", "putty.exe"),
    ),
    KnownTool(
        "Tera Term",
        "Serial terminal",
        "Serial terminal",
        (),
        windows_install=("teraterm5", "ttermpro.exe"),
        platforms=(WINDOWS,),
    ),
    KnownTool(
        "KiCad",
        "EDA",
        "Schematic and PCB design",
        ("kicad",),
        macos_app="KiCad",
        windows_install=("KiCad/*/bin", "kicad.exe"),
    ),
    KnownTool(
        "qv4l2",
        "Camera",
        "V4L2 device control — handy for checking camera formats",
        ("qv4l2", "v4l2-ctl"),
        platforms=(LINUX,),
    ),
)

# --- platform-specific lookups (all injectable for cross-platform testing) ---

MacosAppFinder = Callable[[str], str | None]
WindowsInstallFinder = Callable[[tuple[str, str]], str | None]


def _find_macos_app(app: str) -> str | None:
    """Return the bundle path for ``<app>.app`` under a standard Applications dir."""
    for base in ("/Applications", os.path.expanduser("~/Applications")):
        bundle = Path(base) / f"{app}.app"
        if bundle.exists():
            return str(bundle)
    return None


def _windows_program_files_roots() -> list[str]:
    """The Program Files roots, from the environment (with sane fallbacks)."""
    roots = [
        os.environ[env]
        for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")
        if os.environ.get(env)
    ]
    return roots or [r"C:\Program Files", r"C:\Program Files (x86)"]


def _find_windows_install(install: tuple[str, str]) -> str | None:
    """Return the exe path for a ``(subpath, exe)`` under any Program Files root.

    ``subpath`` may contain a glob (KiCad installs under a version directory), so
    each root is searched with :meth:`Path.glob` when it does.
    """
    subpath, exe = install
    for root in _windows_program_files_roots():
        base = Path(root)
        dirs = base.glob(subpath) if "*" in subpath else [base / subpath]
        for candidate in dirs:
            exe_path = candidate / exe
            if exe_path.exists():
                return str(exe_path)
    return None


def detect_tool(
    tool: KnownTool,
    *,
    which: Callable[[str], str | None] = shutil.which,
    macos_app_finder: MacosAppFinder = _find_macos_app,
    windows_finder: WindowsInstallFinder = _find_windows_install,
    platform: str = sys.platform,
) -> DetectedTool:
    """Look for one catalog entry on this system.

    Tries PATH first (portable, and how ``.exe`` is found on Windows), then the
    platform-native install location. A tool that can't exist on ``platform`` is
    returned not-found without probing.
    """
    if platform not in tool.platforms:
        return DetectedTool(tool)

    for command in tool.commands:
        found = which(command)
        if found:
            # Store the plain command (portable); show where it resolved.
            return DetectedTool(tool, command=command, path=found)

    if platform == MACOS and tool.macos_app:
        bundle = macos_app_finder(tool.macos_app)
        if bundle:
            return DetectedTool(tool, command=f'open -a "{tool.macos_app}"', path=bundle)

    if platform == WINDOWS and tool.windows_install:
        exe = windows_finder(tool.windows_install)
        if exe:
            # Quote the path — Program Files has a space.
            return DetectedTool(tool, command=f'"{exe}"', path=exe)

    return DetectedTool(tool)


def detect_tools(
    tools: tuple[KnownTool, ...] = KNOWN_TOOLS,
    *,
    which: Callable[[str], str | None] = shutil.which,
    macos_app_finder: MacosAppFinder = _find_macos_app,
    windows_finder: WindowsInstallFinder = _find_windows_install,
    platform: str = sys.platform,
) -> list[DetectedTool]:
    """Scan the catalog for this platform; found tools first, grouped by name.

    Tools that can't exist on ``platform`` are omitted entirely — a Windows bench
    shouldn't be shown a list of Linux-only tools it can never install.
    """
    results = [
        detect_tool(
            tool,
            which=which,
            macos_app_finder=macos_app_finder,
            windows_finder=windows_finder,
            platform=platform,
        )
        for tool in tools
        if platform in tool.platforms
    ]
    return sorted(results, key=lambda d: (not d.found, d.tool.category, d.tool.name))


def categories(tools: tuple[KnownTool, ...] = KNOWN_TOOLS) -> list[str]:
    """Distinct categories, in catalog order."""
    seen: list[str] = []
    for tool in tools:
        if tool.category not in seen:
            seen.append(tool.category)
    return seen
