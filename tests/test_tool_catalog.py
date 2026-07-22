"""Tests for the known-tools catalog and detection (no real filesystem lookups)."""

from __future__ import annotations

from benchhud.launcher.catalog import (
    KNOWN_TOOLS,
    KnownTool,
    categories,
    detect_tool,
    detect_tools,
)

_OBV = KnownTool("OpenBoardView", "Boardview", "…", ("openboardview",), macos_app="OpenBoardView")
_NONE = KnownTool("Nothing", "Misc", "…", ("definitely-not-installed",))


def _which(found: dict[str, str]):
    return lambda cmd: found.get(cmd)


def test_catalog_entries_are_sane() -> None:
    assert len(KNOWN_TOOLS) > 5
    for tool in KNOWN_TOOLS:
        assert tool.name and tool.category and tool.description
        assert tool.commands or tool.macos_app  # something to look for


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
    result = detect_tool(_NONE, which=_which({}), platform="linux")
    assert not result.found
    assert result.command is None


def test_detect_macos_app_bundle() -> None:
    result = detect_tool(
        _OBV,
        which=_which({}),
        app_exists=lambda app: app == "OpenBoardView",
        platform="darwin",
    )
    assert result.found
    assert result.command == 'open -a "OpenBoardView"'
    assert result.path == "/Applications/OpenBoardView.app"


def test_macos_bundle_ignored_on_linux() -> None:
    result = detect_tool(_OBV, which=_which({}), app_exists=lambda app: True, platform="linux")
    assert not result.found


def test_detect_tools_puts_found_first() -> None:
    tools = (_NONE, _OBV)
    results = detect_tools(
        tools, which=_which({"openboardview": "/usr/bin/openboardview"}), platform="linux"
    )
    assert results[0].tool.name == "OpenBoardView"
    assert results[0].found
    assert not results[1].found


def test_detect_tools_reports_every_catalog_entry() -> None:
    results = detect_tools(KNOWN_TOOLS, which=_which({}), platform="linux")
    assert len(results) == len(KNOWN_TOOLS)


def test_categories_are_deduped_in_order() -> None:
    result = categories(KNOWN_TOOLS)
    assert "Boardview" in result
    assert len(result) == len(set(result))
