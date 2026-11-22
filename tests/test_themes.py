"""Appearance choices and custom theme files (Qt-free).

A theme file is burnwright's format, so the same file works in burnwright, the
web tools and here. What it can do is set colours and nothing else: every value
is turned into numbers before it gets near a stylesheet.
"""

from __future__ import annotations

import json

import pytest

from benchhud.themes import (
    DARK,
    LIGHT,
    THEME_PROPERTIES,
    BrokenTheme,
    Theme,
    load_themes,
    normalise_choice,
    parse_colour,
    parse_theme,
    qss_colour,
    resolve,
    themes_dir,
)

# --- colours ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#fff", (255, 255, 255, 1.0)),
        ("#12345678", (0x12, 0x34, 0x56, round(0x78 / 255, 3))),
        ("  #1f1f22 ", (0x1F, 0x1F, 0x22, 1.0)),
        ("rgb(10, 20, 30)", (10, 20, 30, 1.0)),
        ("rgb(10 20 30 / 50%)", (10, 20, 30, 0.5)),
        ("rgba(100%, 0%, 0%, 0.25)", (255, 0, 0, 0.25)),
        ("hsl(0, 100%, 50%)", (255, 0, 0, 1.0)),
        ("hsl(0.5turn 100% 50%)", (0, 255, 255, 1.0)),
        ("HSLA(120deg, 100%, 25%, .5)", (0, 128, 0, 0.5)),
    ],
)
def test_colours_a_theme_may_use(text, expected) -> None:
    assert parse_colour(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "red",
        "#ggg",
        "#12345",
        "var(--bw-bg)",
        "url(http://example.com/x.png)",
        "rgb(1, 2)",
        "rgb(1, 2, 3); background: url(x)",
        "calc(1px)",
        "",
        "#" + "f" * 80,
        12,
        None,
    ],
)
def test_anything_else_is_refused(text) -> None:
    assert parse_colour(text) is None


def test_a_see_through_colour_is_written_as_rgba() -> None:
    assert qss_colour((1, 2, 3, 1.0)) == "#010203"
    assert qss_colour((1, 2, 3, 0.5)) == "rgba(1, 2, 3, 50%)"


# --- the built-in looks ---------------------------------------------------------


def test_light_and_dark_name_the_same_colours() -> None:
    assert set(LIGHT) == set(DARK)


def test_dark_is_the_look_benchhud_always_had() -> None:
    """Upgrading mustn't change anyone's HUD: these were the palette before themes."""
    assert DARK["window"] == "#151517"
    assert DARK["panel"] == "#1f1f22"
    assert DARK["accent"] == "#8fb0ff"
    assert DARK["text"] == "#e4e4e8"


def test_light_is_burnwrights_light_set() -> None:
    assert LIGHT["window"] == "#f1f1f4"
    assert LIGHT["panel"] == "#ffffff"
    assert LIGHT["accent"] == "#2f5fcf"
    assert LIGHT["text"] == "#1d1d1f"


def test_every_burnwright_property_is_accepted() -> None:
    assert len(THEME_PROPERTIES) == 34
    assert len(set(THEME_PROPERTIES)) == 34


# --- choices ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("dark", "dark"),
        (" light ", "light"),
        ("system", "system"),
        ("theme:amber.json", "theme:amber.json"),
        ("theme:", "dark"),
        ("purple", "dark"),
        (3, "dark"),
        (None, "dark"),
    ],
)
def test_a_stored_choice_is_tidied(stored, expected) -> None:
    assert normalise_choice(stored) == expected


def test_system_follows_the_operating_system() -> None:
    assert resolve("system", system_dark=True, themes=[]).base == "dark"
    assert resolve("system", system_dark=False, themes=[]).base == "light"


def test_light_and_dark_ignore_the_operating_system() -> None:
    assert resolve("light", system_dark=True, themes=[]).base == "light"
    assert resolve("dark", system_dark=False, themes=[]).base == "dark"


def test_a_theme_whose_file_has_gone_falls_back_to_dark() -> None:
    look = resolve("theme:gone.json", system_dark=False, themes=[])
    assert look.base == "dark" and look.theme is None


def test_a_theme_lays_its_colours_over_its_base() -> None:
    theme = parse_theme("amber.json", json.dumps({"base": "dark", "colors": {"bg": "#120d02"}}))
    assert isinstance(theme, Theme)
    look = resolve("theme:amber.json", system_dark=False, themes=[theme])

    assert look.base == "dark" and look.theme is theme
    assert look.qss()["window"] == "#120d02"
    assert look.qss()["panel"] == DARK["panel"]  # untouched: the base's
    # The menu bar follows the background it's made from.
    assert look.qss()["chrome"] != DARK["chrome"]


def test_a_theme_that_sets_fg_sets_both_text_colours() -> None:
    theme = parse_theme("t.json", json.dumps({"base": "light", "colors": {"--bw-fg": "#102030"}}))
    look = resolve("theme:t.json", system_dark=True, themes=[theme])
    assert look.qss()["text"] == look.qss()["bright"] == "#102030"


def test_the_check_mark_contrasts_with_the_accent() -> None:
    assert resolve("dark", system_dark=True, themes=[]).dark_check  # pale blue accent
    assert not resolve("light", system_dark=True, themes=[]).dark_check  # deep blue


# --- theme files --------------------------------------------------------------


def test_a_burnwright_theme_file_reads_as_is() -> None:
    text = json.dumps(
        {
            "name": "Amber terminal",
            "base": "dark",
            "colors": {"bg": "#120d02", "fg": "#ffb000", "hex-cursor": "#ffb000"},
        }
    )
    theme = parse_theme("amber.json", text)

    assert isinstance(theme, Theme)
    assert theme.name == "Amber terminal"
    assert theme.choice == "theme:amber.json"
    assert theme.ignored == ()
    assert set(theme.colours) == {"bg", "fg", "hex-cursor"}


def test_entries_that_arent_colours_are_dropped_and_said() -> None:
    text = json.dumps(
        {
            "base": "light",
            "extra": 1,
            "colors": {"bg": "url(x)", "font": "#000000", "panel": "#ffffff"},
        }
    )
    theme = parse_theme("odd.json", text)

    assert isinstance(theme, Theme)
    assert set(theme.colours) == {"panel"}
    assert theme.name == "odd"
    assert len(theme.ignored) == 3


@pytest.mark.parametrize(
    "text",
    ["{not json", "[]", '{"base": "sepia"}', '{"base": "dark", "colors": []}'],
)
def test_a_file_that_isnt_a_theme_says_why(text) -> None:
    broken = parse_theme("bad.json", text)
    assert isinstance(broken, BrokenTheme) and broken.reason


def test_load_themes_sorts_by_name_and_keeps_the_broken_apart(tmp_path) -> None:
    (tmp_path / "z.json").write_text(json.dumps({"name": "Alpha", "base": "dark"}))
    (tmp_path / "a.json").write_text(json.dumps({"name": "Beta", "base": "light"}))
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "notes.txt").write_text("not a theme")
    (tmp_path / "huge.json").write_text(" " * (65 * 1024))

    themes, broken = load_themes(tmp_path)

    assert [t.name for t in themes] == ["Alpha", "Beta"]
    assert sorted(b.file for b in broken) == ["broken.json", "huge.json"]


def test_a_missing_folder_is_no_themes(tmp_path) -> None:
    assert load_themes(tmp_path / "nowhere") == ([], [])


def test_the_themes_folder_sits_beside_the_config(tmp_path) -> None:
    config = tmp_path / "benchhud" / "config.toml"
    assert themes_dir(config, environ={}) == tmp_path / "benchhud" / "themes"
    assert themes_dir(config, environ={"BENCHHUD_THEMES_DIR": "/elsewhere"}).as_posix() == (
        "/elsewhere"
    )
