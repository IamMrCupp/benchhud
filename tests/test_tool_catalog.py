"""Tests for the known-tools catalog and detection (no real filesystem lookups).

Every platform-specific lookup is injected, so the macOS and Windows paths are
exercised here on whatever OS CI happens to run.
"""

from __future__ import annotations

from benchhud.launcher.catalog import (
    KNOWN_TOOLS,
    LINUX,
    MACOS,
    WINDOWS,
    KnownTool,
    _find_windows_install,
    categories,
    detect_tool,
    detect_tools,
)

_OBV = KnownTool(
    "OpenBoardView",
    "Boardview",
    "…",
    ("openboardview",),
    macos_app="OpenBoardView",
    windows_install=("OpenBoardView", "OpenBoardView.exe"),
)
_NONE = KnownTool("Nothing", "Misc", "…", ("definitely-not-installed",))


def _which(found: dict[str, str]):
    return lambda cmd: found.get(cmd)


def test_catalog_entries_are_sane() -> None:
    assert len(KNOWN_TOOLS) > 5
    for tool in KNOWN_TOOLS:
        assert tool.name and tool.category and tool.description
        assert tool.platforms  # applies somewhere
        # There's a way to look for it on every platform it claims.
        assert tool.commands or tool.macos_app or tool.windows_install


def test_every_platform_has_at_least_one_tool() -> None:
    """No platform should open the picker to an empty list."""
    for platform in (LINUX, MACOS, WINDOWS):
        assert any(platform in t.platforms for t in KNOWN_TOOLS)


# --- PATH detection ---------------------------------------------------------


def test_detect_finds_command_on_path() -> None:
    result = detect_tool(_OBV, which=_which({"openboardview": "/usr/bin/openboardview"}))
    assert result.found
    assert result.command == "openboardview"  # portable command, not the raw path
    assert result.path == "/usr/bin/openboardview"


def test_detect_prefers_first_candidate() -> None:
    tool = KnownTool("DSView", "Logic analyzer", "…", ("DSView", "dsview"))
    result = detect_tool(tool, which=_which({"DSView": "/a/DSView", "dsview": "/b/dsview"}))
    assert result.command == "DSView"


def test_detect_falls_back_to_second_candidate() -> None:
    tool = KnownTool("DSView", "Logic analyzer", "…", ("DSView", "dsview"))
    result = detect_tool(tool, which=_which({"dsview": "/b/dsview"}))
    assert result.command == "dsview"


def test_detect_missing_tool() -> None:
    result = detect_tool(_NONE, which=_which({}), platform=LINUX)
    assert not result.found
    assert result.command is None


# --- macOS app bundles ------------------------------------------------------


def test_detect_macos_app_bundle() -> None:
    result = detect_tool(
        _OBV,
        which=_which({}),
        macos_app_finder=lambda app: f"/Applications/{app}.app" if app == "OpenBoardView" else None,
        platform=MACOS,
    )
    assert result.found
    assert result.command == 'open -a "OpenBoardView"'
    assert result.path == "/Applications/OpenBoardView.app"


def test_macos_bundle_ignored_on_linux() -> None:
    result = detect_tool(
        _OBV,
        which=_which({}),
        macos_app_finder=lambda app: "/Applications/x.app",
        platform=LINUX,
    )
    assert not result.found


# --- Windows installs -------------------------------------------------------


def test_detect_windows_install() -> None:
    result = detect_tool(
        _OBV,
        which=_which({}),
        windows_finder=lambda install: r"C:\Program Files\OpenBoardView\OpenBoardView.exe",
        platform=WINDOWS,
    )
    assert result.found
    # Quoted because Program Files has a space.
    assert result.command == r'"C:\Program Files\OpenBoardView\OpenBoardView.exe"'
    assert result.path == r"C:\Program Files\OpenBoardView\OpenBoardView.exe"


def test_windows_install_ignored_on_macos() -> None:
    result = detect_tool(
        _OBV,
        which=_which({}),
        windows_finder=lambda install: r"C:\whatever.exe",
        macos_app_finder=lambda app: None,
        platform=MACOS,
    )
    assert not result.found


def test_windows_finder_scans_program_files(tmp_path, monkeypatch) -> None:
    """The real finder: exe under a Program Files root, incl. a glob subpath."""
    pf = tmp_path / "Program Files"
    (pf / "KiCad" / "8.0" / "bin").mkdir(parents=True)
    exe = pf / "KiCad" / "8.0" / "bin" / "kicad.exe"
    exe.write_text("")
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)

    assert _find_windows_install(("KiCad/*/bin", "kicad.exe")) == str(exe)
    assert _find_windows_install(("KiCad/*/bin", "nope.exe")) is None


# --- platform filtering -----------------------------------------------------


def test_platform_specific_tool_skipped_off_platform() -> None:
    """A Windows-only tool isn't probed (or found) on Linux."""
    xgpro = KnownTool(
        "Xgpro", "Programmer", "…", (), windows_install=("Xgpro", "Xgpro.exe"), platforms=(WINDOWS,)
    )
    probed: list[tuple[str, str]] = []
    result = detect_tool(
        xgpro,
        which=_which({}),
        windows_finder=lambda install: probed.append(install) or r"C:\Xgpro.exe",
        platform=LINUX,
    )
    assert not result.found
    assert probed == []  # not even looked for


def test_detect_tools_omits_inapplicable_platforms() -> None:
    linux_only = KnownTool("L", "Cat", "…", ("l",), platforms=(LINUX,))
    win_only = KnownTool("W", "Cat", "…", (), windows_install=("W", "w.exe"), platforms=(WINDOWS,))
    results = detect_tools((linux_only, win_only), which=_which({}), platform=LINUX)
    assert [d.tool.name for d in results] == ["L"]


def test_real_catalog_has_no_windows_only_tools_on_macos() -> None:
    names = {d.tool.name for d in detect_tools(KNOWN_TOOLS, which=_which({}), platform=MACOS)}
    assert "Xgpro" not in names  # Windows-only
    assert "GtkTerm" not in names  # Linux-only
    assert "OpenBoardView" in names  # cross-platform


# --- ordering + grouping ----------------------------------------------------


def test_detect_tools_puts_found_first() -> None:
    tools = (_NONE, _OBV)
    results = detect_tools(
        tools, which=_which({"openboardview": "/usr/bin/openboardview"}), platform=LINUX
    )
    assert results[0].tool.name == "OpenBoardView"
    assert results[0].found
    assert not results[1].found


def test_detect_tools_reports_every_applicable_entry() -> None:
    applicable = [t for t in KNOWN_TOOLS if LINUX in t.platforms]
    results = detect_tools(KNOWN_TOOLS, which=_which({}), platform=LINUX)
    assert len(results) == len(applicable)


def test_categories_are_deduped_in_order() -> None:
    result = categories(KNOWN_TOOLS)
    assert "Boardview" in result
    assert len(result) == len(set(result))
