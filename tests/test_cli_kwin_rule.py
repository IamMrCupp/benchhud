"""``benchhud kwin-rule`` writes the KWin rule that opens the HUD on the big display (#529)."""

from __future__ import annotations

import json

import pytest

from benchhud import app, cli_kwin_rule
from benchhud.cli_kwin_rule import Display, KwinRuleError, choose, displays, main

# The bench's own layout (repair-bench, 2026-11-23): sizes in pixels, positions scaled.
BENCH = {
    "outputs": [
        {
            "name": "HDMI-A-2",
            "enabled": True,
            "connected": True,
            "pos": {"x": 1707, "y": 0},
            "size": {"width": 3840, "height": 2160},
            "scale": 1.25,
            "rotation": 1,
        },
        {
            "name": "HDMI-A-1",
            "enabled": True,
            "connected": True,
            "pos": {"x": 0, "y": 404},
            "size": {"width": 1280, "height": 720},
            "scale": 0.75,
            "rotation": 1,
        },
    ]
}


class FakeKde:
    """kscreen-doctor, kreadconfig6, kwriteconfig6 and qdbus6 over a dict."""

    def __init__(self, layout=BENCH, rules=None, fail=(), version="6.6.6"):
        self.version = version
        self.layout = layout
        self.rules: dict[str, dict[str, str]] = {g: dict(k) for g, k in (rules or {}).items()}
        self.fail = set(fail)
        self.reloads = 0
        self.writes: list[tuple[str, str, str]] = []

    def which(self, tool):
        return None if tool in self.fail else f"/usr/bin/{tool}"

    def run(self, command):
        tool, args = command[0], list(command[1:])
        if f"run:{tool}" in self.fail:
            raise KwinRuleError(f"{tool}: it broke")
        if tool in {"kwin_wayland", "kwin_x11"}:
            assert args == ["--version"]
            return f"kwin {self.version}\n"
        if tool == "kscreen-doctor":
            return self.layout if isinstance(self.layout, str) else json.dumps(self.layout)
        if tool in {"kreadconfig6", "kwriteconfig6"}:
            assert args[:2] == ["--file", "kwinrulesrc"]
            group, key = args[3], args[5]
            if tool == "kreadconfig6":
                return self.rules.get(group, {}).get(key, "") + "\n"
            assert args[6] == "--" and len(args) == 8
            self.rules.setdefault(group, {})[key] = args[7]
            self.writes.append((group, key, args[7]))
            return ""
        if tool == "qdbus6":
            assert args == ["org.kde.KWin", "/KWin", "reconfigure"]
            self.reloads += 1
            return ""
        raise AssertionError(command)


def go(kde, *argv, platform="linux"):
    out: list[str] = []
    err: list[str] = []
    code = main(
        list(argv), out=out.append, err=err.append, platform=platform, run=kde.run, which=kde.which
    )
    return code, out, err


def test_the_biggest_display_wins_by_its_scaled_size() -> None:
    found = displays(json.dumps(BENCH))
    assert [(d.name, round(d.width), round(d.height)) for d in found] == [
        ("HDMI-A-2", 3072, 1728),
        ("HDMI-A-1", 1707, 960),
    ]
    assert choose(found).name == "HDMI-A-2"


def test_a_small_panel_with_more_pixels_does_not_beat_a_bigger_desktop_area() -> None:
    # A 4K panel at 300% is a smaller desktop than a 1440p one at 100%.
    dense = Display("dense", 0, 0, 3840 / 3, 2160 / 3)
    roomy = Display("roomy", 1280, 0, 2560, 1440)
    assert choose([dense, roomy]).name == "roomy"


def test_disabled_and_unplugged_displays_are_left_out() -> None:
    layout = {
        "outputs": [
            {**BENCH["outputs"][0], "enabled": False},
            {**BENCH["outputs"][1], "connected": False, "name": "gone"},
            BENCH["outputs"][1],
        ]
    }
    assert [d.name for d in displays(json.dumps(layout))] == ["HDMI-A-1"]


def test_a_new_rule_is_written_whole_and_listed() -> None:
    kde = FakeKde()
    code, out, err = go(kde)
    assert (code, err) == (0, [])
    assert kde.rules["benchhud-display"] == {
        "Description": "benchhud opens on the big display",
        "wmclass": "benchhud",
        "wmclassmatch": "1",
        "wmclasscomplete": "false",
        "types": "1",
        "position": "1707,0",
        "positionrule": "3",
    }
    assert kde.rules["General"] == {"rules": "benchhud-display", "count": "1"}
    assert kde.reloads == 1
    assert "HDMI-A-2 at 1707,0" in out[0]


def test_the_rule_is_listed_only_after_it_is_whole() -> None:
    kde = FakeKde()
    go(kde)
    groups = [group for group, _key, _value in kde.writes]
    assert groups.index("General") > max(i for i, g in enumerate(groups) if g != "General")


def test_other_rules_are_kept_and_counted() -> None:
    obs = {"wmclass": "com.obsproject.Studio", "wmclassmatch": "1", "screen": "1"}
    kde = FakeKde(rules={"General": {"rules": "obs-small", "count": "1"}, "obs-small": obs})
    assert go(kde)[0] == 0
    assert kde.rules["General"] == {"rules": "obs-small,benchhud-display", "count": "2"}
    assert kde.rules["obs-small"] == obs


def test_an_existing_hud_rule_is_reused_and_only_its_position_changes() -> None:
    # The rule on repair-bench before this command existed.
    mine = {
        "Description": "benchhud always on the big display (HDMI-A-2)",
        "wmclass": "benchhud",
        "wmclassmatch": "1",
        "screen": "0",
        "screenrule": "2",
        "size": "1983,1684",
        "sizerule": "3",
        "position": "0,0",
        "positionrule": "2",
    }
    kde = FakeKde(
        rules={
            "General": {"rules": "benchhud-big-display", "count": "1"},
            "benchhud-big-display": mine,
        }
    )
    code, out, _err = go(kde)
    assert code == 0
    assert kde.rules["benchhud-big-display"] == {**mine, "position": "1707,0", "positionrule": "3"}
    assert kde.rules["General"] == {"rules": "benchhud-big-display", "count": "1"}
    assert set(kde.rules) == {"General", "benchhud-big-display"}
    assert "[benchhud-big-display]" in out[0]


def test_a_rule_that_only_contains_the_class_is_not_taken_over() -> None:
    # wmclassmatch 2 is "contains": that rule may be for more than the HUD.
    loose = {"wmclass": "benchhud", "wmclassmatch": "2", "position": "5,5"}
    kde = FakeKde(rules={"General": {"rules": "loose", "count": "1"}, "loose": loose})
    assert go(kde)[0] == 0
    assert kde.rules["loose"] == loose
    assert kde.rules["General"]["rules"] == "loose,benchhud-display"


def test_a_group_name_already_taken_by_another_rule_is_not_reused() -> None:
    other = {"wmclass": "something-else", "wmclassmatch": "1"}
    kde = FakeKde(
        rules={"General": {"rules": "benchhud-display", "count": "1"}, "benchhud-display": other}
    )
    assert go(kde)[0] == 0
    assert kde.rules["benchhud-display"] == other
    assert kde.rules["benchhud-display-2"]["position"] == "1707,0"


def test_running_it_twice_changes_nothing_more() -> None:
    kde = FakeKde()
    go(kde)
    before = {g: dict(k) for g, k in kde.rules.items()}
    assert go(kde)[0] == 0
    assert kde.rules == before


def test_output_picks_a_display_by_name() -> None:
    for argv in (("--output", "HDMI-A-1"), ("--output=HDMI-A-1",)):
        kde = FakeKde()
        assert go(kde, *argv)[0] == 0
        assert kde.rules["benchhud-display"]["position"] == "0,404"


def test_an_unknown_display_name_writes_nothing_and_lists_the_real_ones() -> None:
    kde = FakeKde()
    code, _out, err = go(kde, "--output", "DP-9")
    assert code == 1 and kde.writes == [] and kde.reloads == 0
    assert "HDMI-A-2, HDMI-A-1" in err[0]


def test_dry_run_writes_nothing() -> None:
    kde = FakeKde()
    code, out, _err = go(kde, "--dry-run")
    assert code == 0 and kde.writes == [] and kde.reloads == 0
    assert out == ["would set a new rule to open benchhud on HDMI-A-2 at 1707,0"]


@pytest.mark.parametrize(
    "layout",
    [
        "not json",
        "{}",
        '{"outputs": [{"enabled": true}]}',
        '{"outputs": []}',
        '{"outputs": null}',
        '{"outputs": 5}',
    ],
)
def test_a_layout_that_cannot_be_read_writes_nothing(layout) -> None:
    kde = FakeKde(layout=layout)
    code, _out, err = go(kde)
    assert code == 1 and kde.writes == []
    assert err and err[0].startswith("benchhud kwin-rule: ")


def test_a_missing_kde_tool_is_named() -> None:
    kde = FakeKde(fail={"kwriteconfig6"})
    code, _out, err = go(kde)
    assert code == 1 and kde.writes == []
    assert "kwriteconfig6" in err[0]


def test_a_tool_that_fails_is_reported_not_raised() -> None:
    kde = FakeKde(fail={"run:kscreen-doctor"})
    code, _out, err = go(kde)
    assert code == 1 and err == ["benchhud kwin-rule: kscreen-doctor: it broke"]


def test_no_way_to_reload_still_writes_the_rule_and_says_so() -> None:
    kde = FakeKde(fail={"qdbus6", "qdbus-qt6", "qdbus"})
    code, out, _err = go(kde)
    assert code == 0 and kde.rules["benchhud-display"]["position"] == "1707,0"
    assert "next login" in out[-1]


def test_a_reload_that_fails_still_counts_as_written() -> None:
    kde = FakeKde(fail={"run:qdbus6"})
    code, out, _err = go(kde)
    assert code == 0 and "next login" in out[-1]


def test_not_linux() -> None:
    kde = FakeKde()
    code, _out, err = go(kde, platform="darwin")
    assert code == 1 and kde.writes == [] and "Linux only" in err[0]


def test_an_unknown_argument_is_refused() -> None:
    kde = FakeKde()
    code, _out, err = go(kde, "--force")
    assert code == 2 and kde.writes == [] and "--force" in err[0]


def test_help_needs_no_desktop() -> None:
    code, out, _err = go(FakeKde(fail=set(cli_kwin_rule.TOOLS)), "--help", platform="darwin")
    assert code == 0 and "kwin-rule" in out[0]


def test_run_tool_turns_a_missing_program_into_an_error() -> None:
    with pytest.raises(KwinRuleError, match="benchhud-no-such-tool"):
        cli_kwin_rule.run_tool(["benchhud-no-such-tool"])


def test_run_tool_reports_a_failed_exit() -> None:
    import sys

    with pytest.raises(KwinRuleError, match="nope"):
        cli_kwin_rule.run_tool(
            [sys.executable, "-c", "import sys; sys.stderr.write('nope'); sys.exit(3)"]
        )


def test_the_command_is_wired_into_the_entry_point(monkeypatch) -> None:
    assert "kwin-rule" in app.COMMANDS
    seen = []
    monkeypatch.setattr(cli_kwin_rule, "main", lambda argv: seen.append(argv) or 7)
    assert app.main(["kwin-rule", "--dry-run"]) == 7
    assert seen == [["--dry-run"]]


# --- the Plasma 6.8 file: every group is a rule, [General] Order has the order ---

OBS_RULE = {"wmclass": "com.obsproject.Studio", "wmclassmatch": "1", "screen": "1"}
OLD_HUD_RULE = {"wmclass": "benchhud", "wmclassmatch": "1", "position": "0,0", "positionrule": "2"}


def new_format(**groups) -> FakeKde:
    rules = {"General": {"Order": ",".join(groups)}, **groups}
    return FakeKde(rules=rules, version="6.8.3")


def test_new_format_never_gets_a_rules_list() -> None:
    """A rules list there makes KWin delete every rule it doesn't name."""
    kde = new_format(obs=OBS_RULE)
    assert go(kde)[0] == 0
    assert kde.rules["General"] == {"Order": "obs"}
    assert kde.rules["obs"] == OBS_RULE
    assert kde.rules["benchhud-display"]["position"] == "1707,0"
    assert not [w for w in kde.writes if w[0] == "General"]


def test_new_format_reuses_the_existing_hud_rule() -> None:
    kde = new_format(obs=OBS_RULE, mine=OLD_HUD_RULE)
    code, out, _err = go(kde)
    assert code == 0 and "[mine]" in out[0]
    assert kde.rules["mine"] == {**OLD_HUD_RULE, "position": "1707,0", "positionrule": "3"}
    assert set(kde.rules) == {"General", "obs", "mine"}
    assert kde.rules["General"] == {"Order": "obs,mine"}


def test_new_format_dry_run_names_the_existing_rule() -> None:
    kde = new_format(mine=OLD_HUD_RULE)
    assert go(kde, "--dry-run")[1] == [
        "would set the existing rule [mine] to open benchhud on HDMI-A-2 at 1707,0"
    ]
    assert kde.writes == []


@pytest.mark.parametrize("version", ["6.7.90", "6.8.0", "7.0.1"])
def test_an_empty_file_on_a_new_kwin_gets_the_group_alone(version) -> None:
    kde = FakeKde(version=version)
    assert go(kde)[0] == 0
    assert set(kde.rules) == {"benchhud-display"}


@pytest.mark.parametrize("version", ["6.6.6", "6.7.6", "5.27.11"])
def test_an_empty_file_on_an_old_kwin_gets_the_list_too(version) -> None:
    kde = FakeKde(version=version)
    assert go(kde)[0] == 0
    assert kde.rules["General"] == {"rules": "benchhud-display", "count": "1"}


def test_an_empty_file_and_no_kwin_version_writes_nothing() -> None:
    kde = FakeKde(fail={"kwin_wayland", "kwin_x11"})
    code, _out, err = go(kde)
    assert code == 1 and kde.writes == [] and "version" in err[0]


def test_kwin_x11_answers_when_there_is_no_kwin_wayland() -> None:
    kde = FakeKde(fail={"kwin_wayland"}, version="6.8.3")
    assert go(kde)[0] == 0 and "General" not in kde.rules


def test_a_file_from_before_rule_lists_is_refused() -> None:
    numbered = {"General": {"count": "2"}, "1": OBS_RULE, "2": OLD_HUD_RULE}
    kde = FakeKde(rules=numbered)
    code, _out, err = go(kde)
    assert code == 1 and kde.writes == [] and "Window Rules" in err[0]


def test_an_unlisted_group_with_our_name_is_not_overwritten() -> None:
    # In the new layout a group can exist without being in Order yet.
    kde = FakeKde(
        rules={"General": {"Order": "obs"}, "obs": OBS_RULE, "benchhud-display": OBS_RULE},
        version="6.8.3",
    )
    assert go(kde)[0] == 0
    assert kde.rules["benchhud-display"] == OBS_RULE
    assert kde.rules["benchhud-display-2"]["wmclass"] == "benchhud"


@pytest.mark.parametrize(
    "extra",
    [
        {"Enabled": "false"},
        {"titlematch": "1", "title": "x"},
        {"windowrolematch": "2"},
        {"clientmachinematch": "1"},
    ],
)
def test_a_rule_that_is_off_or_narrowed_is_not_taken_over(extra) -> None:
    theirs = {**OLD_HUD_RULE, **extra}
    kde = FakeKde(rules={"General": {"rules": "theirs", "count": "1"}, "theirs": theirs})
    assert go(kde)[0] == 0
    assert kde.rules["theirs"] == theirs
    assert kde.rules["General"]["rules"] == "theirs,benchhud-display"


def test_a_reused_rule_with_a_screen_setting_gets_a_note() -> None:
    mine = {**OLD_HUD_RULE, "screen": "0", "screenrule": "2"}
    kde = FakeKde(rules={"General": {"rules": "mine", "count": "1"}, "mine": mine})
    code, out, _err = go(kde)
    assert code == 0 and "screen=0" in out[1] and kde.rules["mine"]["screenrule"] == "2"


def test_a_new_rule_gets_no_screen_note() -> None:
    assert not [line for line in go(FakeKde())[1] if "screen" in line]


def test_a_display_left_of_the_first_one_is_written() -> None:
    layout = {"outputs": [{**BENCH["outputs"][0], "pos": {"x": -1920, "y": -40}}]}
    kde = FakeKde(layout=layout)
    assert go(kde)[0] == 0
    assert kde.rules["benchhud-display"]["position"] == "-1920,-40"


@pytest.mark.parametrize("argv", [("--output",), ("--output", "--dry-run"), ("--output=",)])
def test_output_without_a_name_is_refused(argv) -> None:
    kde = FakeKde()
    code, _out, err = go(kde, *argv)
    assert code == 2 and kde.writes == [] and "needs a display name" in err[0]


def test_run_tool_survives_output_that_is_not_utf8() -> None:
    import sys

    text = cli_kwin_rule.run_tool(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xffb')"]
    )
    assert text.startswith("a") and text.endswith("b")
