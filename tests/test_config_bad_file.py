"""A config file that's wrong must not stop benchhud starting (#475).

Three ways it used to: a ``[[tools]]`` block added by hand to a saved file, an
escape character pasted into any text setting, and a file that isn't valid
TOML at all. Plus the ones found on the way: a value of the wrong type, a
section of the wrong shape, and a save that fails halfway.

What must *not* change is here too: a config that worked before still means
what it meant — ``enabled = 1``, ``fps = 29.97`` — and the example config
loads without a murmur.

The last test runs the app's real entry point against a broken file.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import stat
import tomllib
from pathlib import Path

import pytest

from benchhud import app, cli_config
from benchhud import config as cfg
from benchhud.config import CameraConfig, ChipSourceConfig, Config, ConfigError, ToolConfig
from benchhud.devices import camnames
from benchhud.shell import config_problem
from benchhud.shell.main_window import MainWindow

# What a config saved before this fix looks like once someone has followed
# config.example.toml and added a tool by hand.
_HAND_EDITED = """\
tools = []

[shell]
window_width = 1000

[intake]
token = "keep-me"

[[tools]]
name = "OpenBoardView"
command = "openboardview"
"""


@pytest.fixture
def broken(tmp_path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(_HAND_EDITED)
    return path


class _FakeScanner:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


# --- 1. an empty list is not written ---------------------------------------


def test_a_fresh_config_saves_no_empty_lists(tmp_path) -> None:
    path = cfg.save(Config(), tmp_path / "config.toml")
    saved = tomllib.loads(path.read_text())
    assert "tools" not in saved
    assert "cameras" not in saved
    assert "sources" not in saved["chips"]
    assert cfg.load(path) == Config()


@pytest.mark.parametrize(
    ("block", "read"),
    [
        (
            '[[tools]]\nname = "OpenBoardView"\ncommand = "openboardview"\n',
            lambda c: c.tools == [ToolConfig(name="OpenBoardView", command="openboardview")],
        ),
        (
            '[[cameras]]\nname = "Bench cam"\ndevice = "/dev/video20"\n',
            lambda c: c.cameras == [CameraConfig(name="Bench cam", device="/dev/video20")],
        ),
        (
            '[[chips.sources]]\nname = "community"\npath = "~/src/chip-data"\n',
            lambda c: c.chips.sources == [ChipSourceConfig("community", path="~/src/chip-data")],
        ),
    ],
)
def test_a_block_added_by_hand_to_a_saved_config_loads(tmp_path, block: str, read) -> None:
    path = cfg.save(Config(), tmp_path / "config.toml")
    path.write_text(path.read_text() + "\n" + block)
    assert read(cfg.load(path))


def test_lists_with_something_in_them_are_still_saved(tmp_path) -> None:
    config = Config()
    config.tools = [ToolConfig(name="flashrom", command="flashrom", terminal=True)]
    config.cameras = [CameraConfig(name="Bench cam", device="/dev/video20")]
    config.chips.sources = [ChipSourceConfig("community", path="~/src/chip-data")]
    path = cfg.save(config, tmp_path / "config.toml")
    assert cfg.load(path) == config


# --- 2. every saved string loads back --------------------------------------


def _set_everywhere(text: str) -> Config:
    config = Config()
    config.boards.library_dir = text
    config.intake.token = text
    config.terminal.shell = text
    config.shell.layout = text
    config.boards.extensions = [text, "brd"]
    config.tools = [ToolConfig(name=text, command=text, scene=text)]
    config.cameras = [CameraConfig(name=text, device=text)]
    config.chips.sources = [ChipSourceConfig(text, path=text)]
    return config


@pytest.mark.parametrize("pasted", ["a\x1bb", "\x1b[31mred\x1b[0m", "\\\x1b", "x\x1be"])
def test_an_escape_character_does_not_break_the_next_start(tmp_path, pasted: str) -> None:
    path = cfg.save(_set_everywhere(pasted), tmp_path / "config.toml")
    loaded = cfg.load(path)  # used to raise: tomlkit writes \e, tomllib refuses it
    assert loaded == _set_everywhere(pasted.replace("\x1b", ""))


def test_a_setting_that_is_only_an_escape_character_saves_as_empty(tmp_path) -> None:
    config = Config()
    config.boards.library_dir = "\x1b"
    config.intake.token = "\x1b\x1b"
    path = cfg.save(config, tmp_path / "config.toml")
    assert cfg.load(path) == Config()


def test_an_escape_character_in_the_name_of_a_thing_is_dropped_too(tmp_path) -> None:
    # A camera shot's name is free text, and it's a key in the file.
    config = Config()
    config.cameras = [
        CameraConfig(name="Bench", device="/dev/video20", shots={"Fa\x1bce": {"zoom": 2}})
    ]
    path = cfg.save(config, tmp_path / "config.toml")  # used to raise on every save
    assert cfg.load(path).cameras == [
        CameraConfig(name="Bench", device="/dev/video20", shots={"Face": {"zoom": 2}})
    ]


def test_a_lone_surrogate_does_not_break_the_save(tmp_path) -> None:
    path = tmp_path / "config.toml"
    cfg.save(_set_everywhere("keep"), path)
    cfg.save(_set_everywhere("a\ud800b\udfffc"), path)  # used to die mid-write, file emptied
    assert cfg.load(path) == _set_everywhere("abc")


@pytest.mark.parametrize(
    "text",
    [
        "C:\\europe\\tools",  # a real backslash before an e is not an escape character
        "\\e",
        "quote \" and ' and \\",
        "tab\there",
        "line\nbreak",
        "nul\x00del\x7fbell\x07",
        "caf\u00e9 \u2603 \U0001f600",
        "\u2028\u2029\ufeff",
        " padded ",
        "",
    ],
)
def test_everything_else_comes_back_exactly(tmp_path, text: str) -> None:
    config = Config()
    config.boards.library_dir = text
    config.intake.token = text
    config.boards.extensions = [text, "brd"]
    path = cfg.save(config, tmp_path / "config.toml")
    assert cfg.load(path) == config


def test_a_save_that_would_not_load_back_leaves_the_file_alone(tmp_path, monkeypatch) -> None:
    path = cfg.save(_set_everywhere("precious"), tmp_path / "config.toml")
    before = path.read_bytes()
    monkeypatch.setattr(cfg.tomlkit, "dumps", lambda data: 'broken = "\\q"\n')
    with pytest.raises(ConfigError, match="wouldn't load back") as raised:
        cfg.save(Config(), path)
    assert raised.value.path == path
    assert path.read_bytes() == before


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name != "config.toml")


def test_a_write_that_fails_halfway_leaves_the_old_file_whole(tmp_path, monkeypatch) -> None:
    path = cfg.save(_set_everywhere("precious"), tmp_path / "config.toml")
    before = path.read_bytes()

    class DiskFull:
        def __init__(self, fd: int) -> None:
            self.fd = fd

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            os.close(self.fd)

        def write(self, data: bytes) -> None:
            os.write(self.fd, data[:10])  # some of it lands, then:
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(cfg.os, "fdopen", lambda fd, mode: DiskFull(fd))
    with pytest.raises(OSError, match="No space left"):
        cfg.save(Config(), path)
    assert path.read_bytes() == before  # used to be ten bytes long
    assert _leftovers(tmp_path) == []  # and nothing is left lying beside it


def test_a_move_into_place_that_fails_leaves_the_old_file_whole(tmp_path, monkeypatch) -> None:
    path = cfg.save(_set_everywhere("precious"), tmp_path / "config.toml")
    before = path.read_bytes()

    def refuse(src, dst):
        raise PermissionError(13, "in use by another process")

    monkeypatch.setattr(cfg.os, "replace", refuse)
    with pytest.raises(OSError, match="in use"):
        cfg.save(Config(), path)
    assert path.read_bytes() == before
    assert _leftovers(tmp_path) == []


def test_a_good_save_leaves_nothing_beside_the_file(tmp_path) -> None:
    cfg.save(Config(), tmp_path / "config.toml")
    cfg.save(_set_everywhere("text"), tmp_path / "config.toml")
    assert _leftovers(tmp_path) == []
    assert stat.S_IMODE((tmp_path / "config.toml").stat().st_mode) in (
        0o600,
        0o666,
    )  # 0666: Windows


def test_a_config_that_is_a_link_stays_a_link(tmp_path) -> None:
    real = tmp_path / "dotfiles" / "benchhud.toml"
    real.parent.mkdir()
    cfg.save(_set_everywhere("old"), real)
    link = tmp_path / "config" / "config.toml"
    link.parent.mkdir()
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("can't make a symlink here")
    cfg.save(_set_everywhere("new"), link)
    assert link.is_symlink()  # not swapped for a plain file
    assert cfg.load(real) == _set_everywhere("new")  # what it points at is what changed
    assert sorted(p.name for p in link.parent.iterdir()) == ["config.toml"]
    assert sorted(p.name for p in real.parent.iterdir()) == ["benchhud.toml"]


def test_a_file_in_a_folder_it_may_not_add_to_is_written_in_place(tmp_path) -> None:
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("permissions don't keep this process out")
    folder = tmp_path / "locked"
    folder.mkdir()
    path = cfg.save(_set_everywhere("old"), folder / "config.toml")
    folder.chmod(0o500)  # the file can be written; nothing can be made beside it
    try:
        cfg.save(_set_everywhere("new"), path)
        assert cfg.load(path) == _set_everywhere("new")
    finally:
        folder.chmod(0o700)


def test_a_save_that_would_not_load_back_creates_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cfg.tomlkit, "dumps", lambda data: "not toml = = =\n")
    with pytest.raises(ConfigError):
        cfg.save(Config(), tmp_path / "new" / "config.toml")
    assert not (tmp_path / "new").exists()


# --- 3. a file that can't be read is an error with the file and line in it -


def test_a_file_that_is_not_there_is_a_first_run() -> None:
    assert cfg.load("/nowhere/at/all/config.toml") == Config()


def test_an_empty_file_is_defaults(tmp_path) -> None:
    (tmp_path / "config.toml").write_text("")
    assert cfg.load(tmp_path / "config.toml") == Config()


def test_a_file_that_is_not_toml_says_which_file_and_which_line(broken) -> None:
    with pytest.raises(ConfigError) as raised:
        cfg.load(broken)
    problem = raised.value
    assert problem.path == broken
    assert "line 9" in problem.reason
    assert str(problem) == f"{broken}: {problem.reason}"
    assert isinstance(problem, ValueError)


def test_the_empty_list_trap_is_explained_in_plain_words(broken) -> None:
    with pytest.raises(ConfigError) as raised:
        cfg.load(broken)
    assert "An earlier line sets `tools = []`" in raised.value.reason
    assert "delete that line to use [[tools]] blocks" in raised.value.reason


@pytest.mark.parametrize(
    "line",
    ["tools = []", "tools=[]", "  tools = [ ]  ", "tools = []  # none yet", "cameras = []"],
)
def test_the_hint_is_given_for_an_empty_list_however_it_is_written(tmp_path, line: str) -> None:
    name = line.split("=")[0].strip()
    path = tmp_path / "config.toml"
    path.write_text(f'{line}\n\n[[{name}]]\nname = "x"\ncommand = "y"\ndevice = "z"\n')
    with pytest.raises(ConfigError) as raised:
        cfg.load(path)
    assert f"An earlier line sets `{name} = []`" in raised.value.reason


@pytest.mark.parametrize(
    "content",
    [
        # The parser says the same words for these, and "delete that line" would be wrong.
        'shell = {window_width = 5}\nshell.layout = "x"\n',
        'tools = [{name = "x", command = "y"}]\n\n[[tools]]\nname = "z"\ncommand = "w"\n',
    ],
)
def test_the_hint_is_not_given_where_it_would_mislead(tmp_path, content: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigError) as raised:
        cfg.load(path)
    assert "Cannot mutate immutable namespace" in raised.value.reason
    assert "delete that line" not in raised.value.reason


@pytest.mark.parametrize(
    "content",
    [
        b"[shell\nwindow_width = 1",
        b'token = "unterminated\n',
        b"window_width = = 1\n",
        b'[intake]\ntoken = "a"\ntoken = "b"\n',
        b"\xff\xfe not utf-8 \x00",
        b'path = "a\\eb"\n',  # what an old save of an escape character left behind
    ],
)
def test_other_broken_files_raise_the_same_error(tmp_path, content: bytes) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(content)
    with pytest.raises(ConfigError) as raised:
        cfg.load(path)
    assert raised.value.path == path
    assert raised.value.reason


@pytest.mark.parametrize(
    "content",
    [
        "x = " + "[" * 100_000 + "]" * 100_000,  # nested past the parser
        "x = " + "9" * 5000,  # a number past what Python will read
    ],
)
def test_the_parser_running_out_of_road_is_the_same_error(tmp_path, content: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigError, match="can't be parsed"):
        cfg.load(path)


def test_a_number_too_big_for_its_field_is_just_a_bad_value(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"[instruments]\npoll_interval = {'9' * 400}\nmax_current = 2.5\n")
    config = cfg.load(path)  # used to be an OverflowError
    assert config.instruments.poll_interval == Config().instruments.poll_interval
    assert config.instruments.max_current == 2.5


def test_a_byte_order_mark_is_not_an_error(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xef\xbb\xbf" + b'[intake]\ntoken = "abc"\n')
    assert cfg.load(path).intake.token == "abc"


def test_from_dict_failing_anyway_is_still_the_file_s_error(tmp_path, monkeypatch) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[shell]\nwindow_width = 1000\n")

    def broken(data):
        raise KeyError("something new")

    monkeypatch.setattr(Config, "from_dict", staticmethod(broken))
    with pytest.raises(ConfigError, match="isn't in the shape") as raised:
        cfg.load(path)
    assert raised.value.path == path


def test_a_folder_where_the_file_should_be_is_an_error_not_defaults(tmp_path) -> None:
    (tmp_path / "config.toml").mkdir()
    with pytest.raises(ConfigError, match="can't be read") as raised:
        cfg.load(tmp_path / "config.toml")
    assert str(tmp_path) not in raised.value.reason  # the path is said once, not twice


def test_a_file_it_may_not_read_is_an_error_not_defaults(tmp_path) -> None:
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("permissions don't keep this process out")
    path = tmp_path / "config.toml"
    path.write_text('[intake]\ntoken = "keep-me"\n')
    path.chmod(0)
    try:
        with pytest.raises(ConfigError, match="can't be read"):
            cfg.load(path)
    finally:
        path.chmod(0o600)


# --- a value of the wrong type, a section of the wrong shape ---------------


_SECTIONS = [f.name for f in dataclasses.fields(Config)]


@pytest.mark.parametrize("junk", [5, "text", True, 1.5, [1, 2], [], [{"a": 1}]])
@pytest.mark.parametrize("section", _SECTIONS)
def test_a_section_of_the_wrong_shape_is_the_same_as_not_there(section: str, junk) -> None:
    config = Config.from_dict({section: junk})
    assert getattr(config, section) == getattr(Config(), section)


@pytest.mark.parametrize(
    "data",
    [
        {"shell": {"logging": 5}},
        {"panels": {"scope": "x", "thermal": [1], "telemetry": True}},
        {"chips": {"sources": "community", "datasheet_dir": 5}},
        {"chips": {"sources": [5, "x", None, {"path": "no name"}]}},
        {"tools": [5, "x", {"name": 5, "command": "y"}, {"name": "x", "command": None}, {}]},
        {"tools": {"name": "x", "command": "y"}},
        {"cameras": {"name": "x"}},
    ],
)
def test_nested_things_of_the_wrong_shape_are_the_same_as_not_there(data) -> None:
    assert Config.from_dict(data) == Config()


@pytest.mark.parametrize("data", [None, 5, "x", [], [("shell", {})]])
def test_a_config_that_is_not_a_table_at_all_is_defaults(data) -> None:
    assert Config.from_dict(data) == Config()


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("shell", "window_width", "wide"),  # used to reach Qt and stop the window opening
        ("shell", "window_width", True),
        ("shell", "window_width", [1200]),
        ("shell", "window_width", float("inf")),
        ("shell", "window_width", float("nan")),
        ("shell", "layout", 5),
        ("output", "enabled", 2),
        ("output", "enabled", -1),
        ("output", "enabled", 1.0),
        ("output", "enabled", "yes"),
        ("output", "device", 10),
        ("output", "fps", "15"),
        ("browser_sources", "port", "8780"),
        ("instruments", "poll_interval", "fast"),
        ("instruments", "poll_interval", True),
        ("instruments", "poll_interval", float("nan")),
        ("instruments", "poll_interval", float("inf")),
        ("instruments", "poll_interval", 10**400),  # too big to be a decimal at all
        ("instruments", "max_voltage", None),
        ("boards", "extensions", "obv"),
        ("boards", "extensions", ["obv", 5]),
        ("boards", "library_dir", ["~/boards"]),
        ("intake", "token", 12345),
    ],
)
def test_a_value_of_the_wrong_type_keeps_the_default(section: str, key: str, value) -> None:
    config = Config.from_dict({section: {key: value}})
    assert getattr(getattr(config, section), key) == getattr(getattr(Config(), section), key)
    assert config == Config()


def test_numbers_are_taken_either_way_round() -> None:
    config = Config.from_dict(
        {
            "output": {"fps": 30.0},  # a whole number written with a point
            "instruments": {"poll_interval": 1, "max_voltage": 12},  # a decimal written without
        }
    )
    assert (config.output.fps, type(config.output.fps)) == (30, int)
    assert (config.instruments.poll_interval, type(config.instruments.poll_interval)) == (
        1.0,
        float,
    )
    assert config.instruments.max_voltage == 12.0


# --- what worked before still works -----------------------------------------


def test_a_fraction_for_a_whole_number_is_cut_not_thrown_away() -> None:
    # `fps = 29.97` ran at 29 before; resetting it to the default would be a
    # setting silently lost on upgrade.
    config = Config.from_dict({"output": {"fps": 29.97}, "shell": {"window_width": 1300.5}})
    assert (config.output.fps, type(config.output.fps)) == (29, int)
    assert (config.shell.window_width, type(config.shell.window_width)) == (1300, int)


@pytest.mark.parametrize(
    ("written", "means"), [(1, True), (0, False), (True, True), (False, False)]
)
def test_a_switch_written_as_one_or_nought_still_means_on_or_off(written, means: bool) -> None:
    config = Config.from_dict(
        {
            "output": {"enabled": written},
            "panels": {"telemetry": {"enabled": written}},
            "now_playing": {"enabled": written},
            "tools": [{"name": "x", "command": "y", "terminal": written}],
        }
    )
    assert config.output.enabled is means
    assert config.panels.telemetry.enabled is means
    assert config.now_playing.enabled is means
    assert config.tools[0].terminal is means


def test_the_example_config_loads_without_a_single_complaint(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.config")
    example = Path(__file__).resolve().parent.parent / "config.example.toml"
    cfg.load(example)
    assert caplog.records == []


def test_a_config_the_app_saved_loads_without_a_single_complaint(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.config")
    full = _set_everywhere("text")
    full.shell.layout = "AAAA"
    assert cfg.load(cfg.save(full, tmp_path / "config.toml")) == full
    assert cfg.load(cfg.save(Config(), tmp_path / "default.toml")) == Config()
    assert caplog.records == []


def test_a_value_that_is_dropped_is_logged_with_its_section_and_key(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.config")
    Config.from_dict(
        {
            "shell": {"window_width": "wide", "logging": {"level": 5}},
            "panels": {"scope": {"width": "x"}},
            "output": {"fps": 30},  # fine: not logged
        }
    )
    said = sorted(record.getMessage() for record in caplog.records)
    assert said == [
        "config: [panels.scope] width is the wrong kind of value; using 0",
        "config: [shell.logging] level is the wrong kind of value; using 'INFO'",
        "config: [shell] window_width is the wrong kind of value; using 1200",
    ]


def test_a_saved_placement_with_no_version_is_still_the_old_placement() -> None:
    assert Config.from_dict({"registration": {"scale": 1.5}}).registration.version == 1
    assert Config.from_dict({"registration": 5}).registration == Config().registration


def test_one_bad_value_does_not_cost_the_good_ones_beside_it() -> None:
    config = Config.from_dict(
        {"shell": {"window_width": "wide", "window_height": 900}, "intake": {"token": "abc"}}
    )
    assert config.shell.window_width == Config().shell.window_width
    assert config.shell.window_height == 900
    assert config.intake.token == "abc"


def test_a_tool_s_odd_fields_fall_back() -> None:
    config = Config.from_dict(
        {"tools": [{"name": "x", "command": "y", "terminal": "yes", "scene": 5}]}
    )
    assert config.tools == [ToolConfig(name="x", command="y", terminal=False, scene="")]


def test_a_window_opens_on_a_config_full_of_wrong_types(qtbot) -> None:
    config = Config.from_dict(
        {
            "shell": {"window_width": "wide", "window_height": None, "layout": 5},
            "output": {"width": "1280", "fps": []},
            "panels": {"scope": {"width": "x"}, "telemetry": {"baud": "fast"}},
            "performance": {"opencv_threads": "many"},
        }
    )
    window = MainWindow(config, scanner=_FakeScanner())
    qtbot.addWidget(window)
    assert window.width() > 0


def test_the_defaults_survive_their_own_round_trip() -> None:
    assert Config.from_dict(Config().to_dict()) == Config()
    full = _set_everywhere("text")
    assert Config.from_dict(full.to_dict()) == full


# --- starting up on a file that can't be read ------------------------------


def test_a_good_or_missing_config_is_no_problem(tmp_path) -> None:
    path = cfg.save(_set_everywhere("text"), tmp_path / "config.toml")
    assert config_problem.load_or_defaults(lambda: cfg.load(path)) == (
        _set_everywhere("text"),
        None,
    )
    missing = tmp_path / "none.toml"
    assert config_problem.load_or_defaults(lambda: cfg.load(missing)) == (Config(), None)


def test_a_broken_config_gives_defaults_and_the_problem(broken) -> None:
    config, problem = config_problem.load_or_defaults(lambda: cfg.load(broken))
    assert config == Config()
    assert isinstance(problem, ConfigError)
    assert problem.path == broken


def test_the_message_says_which_file_what_is_wrong_and_what_happens_next(broken) -> None:
    _, problem = config_problem.load_or_defaults(lambda: cfg.load(broken))
    text = config_problem.explain(problem)
    assert str(broken) in text
    assert "line 9" in text
    assert "Nothing will be saved this session" in text
    assert "Fix it and start benchhud again" in text
    assert "running on default settings" in config_problem.HEADLINE


def test_with_a_problem_nothing_is_saved_and_the_log_says_why(broken, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.shell.config_problem")
    _, problem = config_problem.load_or_defaults(lambda: cfg.load(broken))
    saved: list[Config] = []
    persist = config_problem.saver(saved.append, problem)
    persist(Config())
    persist(Config())
    assert saved == []
    assert len(caplog.records) == 2
    assert str(broken) in caplog.records[0].getMessage()
    assert "left as it is" in caplog.records[0].getMessage()


def test_with_no_problem_saving_is_saving(tmp_path) -> None:
    path = tmp_path / "config.toml"
    persist = config_problem.saver(lambda config: cfg.save(config, path), None)
    persist(_set_everywhere("text"))
    assert cfg.load(path) == _set_everywhere("text")


@pytest.mark.parametrize(
    "failure", [OSError("disk full"), ConfigError(Path("config.toml"), "wouldn't load back")]
)
def test_a_save_that_fails_is_logged_not_raised(failure: Exception, caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.shell.config_problem")

    def save(config: Config) -> None:
        raise failure

    config_problem.saver(save, None)(Config())  # a window closing must not raise
    (record,) = caplog.records
    assert "couldn't save the config" in record.getMessage()
    assert str(failure) in record.getMessage()


def test_the_message_is_shown_without_holding_the_app_up(qtbot, broken) -> None:
    from PySide6.QtWidgets import QMessageBox

    _, problem = config_problem.load_or_defaults(lambda: cfg.load(broken))
    window = MainWindow(scanner=_FakeScanner())
    qtbot.addWidget(window)
    window.show()
    from PySide6.QtCore import Qt

    box = config_problem.show(window, problem)  # returns straight away
    assert isinstance(box, QMessageBox)
    assert box.isVisible()
    assert box.text() == config_problem.HEADLINE
    assert box.informativeText() == config_problem.explain(problem)
    assert box.icon() == QMessageBox.Icon.Warning
    # The path and the line can be copied out, and the box cleans up after itself.
    assert box.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert box.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    box.close()


def test_closing_and_preferences_ok_leave_a_broken_file_alone(qtbot, broken) -> None:
    before = broken.read_bytes()
    config, problem = config_problem.load_or_defaults(lambda: cfg.load(broken))
    persist = config_problem.saver(lambda c: cfg.save(c, broken), problem)
    window = MainWindow(config, on_persist=persist, scanner=_FakeScanner())
    qtbot.addWidget(window)
    dialog = window.open_preferences()
    dialog.accept()
    window.close()
    assert broken.read_bytes() == before


def test_the_real_app_starts_on_a_broken_config_and_touches_nothing(
    qtbot, broken, monkeypatch, caplog
) -> None:
    # The entry point itself, with only the event loop and the process-wide
    # side effects (theme, signal handlers, log setup) stubbed out.
    from PySide6.QtWidgets import QApplication, QMessageBox

    from benchhud import logging_setup
    from benchhud.shell import quit_signals, theme

    before = broken.read_bytes()
    caplog.set_level(logging.ERROR, logger="benchhud.app")
    monkeypatch.setenv(cfg.ENV_VAR, str(broken))
    monkeypatch.setattr(theme, "apply_theme", lambda app, *args: None)
    monkeypatch.setattr(quit_signals, "install", lambda *args: None)
    monkeypatch.setattr(logging_setup, "configure_logging", lambda level: None)
    seen: dict[str, object] = {}

    def instead_of_the_event_loop(*_args) -> int:
        top = QApplication.instance().topLevelWidgets()
        # Other tests' windows may not have been collected yet: this run's is
        # the one that's showing.
        window = next(w for w in top if w.objectName() == "BenchhudMainWindow" and w.isVisible())
        boxes = [w for w in top if isinstance(w, QMessageBox) and w.isVisible()]
        seen["window_shown"] = window.isVisible()
        seen["messages"] = [(box.text(), box.informativeText()) for box in boxes]
        seen["width"] = window._config.shell.window_width
        window.open_preferences().accept()  # an OK in Preferences
        window.close()  # and closing: neither may save
        return 0

    monkeypatch.setattr(QApplication, "exec", staticmethod(instead_of_the_event_loop))

    assert app.main([]) == 0  # used to be a traceback before any window

    assert seen["window_shown"] is True
    ((headline, detail),) = seen["messages"]
    assert headline == config_problem.HEADLINE
    assert str(broken) in detail and "line 9" in detail
    assert seen["width"] == Config().shell.window_width  # defaults, not the file's 1000
    assert broken.read_bytes() == before
    # Said once in the log (which goes to the terminal), with the file and the line.
    (said,) = [r.getMessage() for r in caplog.records if r.name == "benchhud.app"]
    assert config_problem.HEADLINE in said
    assert str(broken) in said and "line 9" in said


# --- the commands ----------------------------------------------------------


def test_config_set_on_a_broken_file_changes_nothing_and_says_so(
    broken, monkeypatch, capsys
) -> None:
    before = broken.read_bytes()
    monkeypatch.setenv(cfg.ENV_VAR, str(broken))
    code = cli_config.main(["set", "intake.base_url", "http://127.0.0.1:8401"])
    assert code == cli_config.EXIT_BAD_CONFIG == 3
    err = capsys.readouterr().err
    assert str(broken) in err
    assert "line 9" in err
    assert "Nothing was changed." in err
    assert broken.read_bytes() == before  # not rewritten from defaults: the token is still there


def test_config_set_that_cannot_write_says_so_and_changes_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    path = cfg.save(_set_everywhere("precious"), tmp_path / "config.toml")
    before = path.read_bytes()
    monkeypatch.setenv(cfg.ENV_VAR, str(path))

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cfg.os, "replace", refuse)
    code = cli_config.main(["set", "intake.base_url", "http://127.0.0.1:8401"])
    assert code == cli_config.EXIT_BAD_CONFIG
    err = capsys.readouterr().err
    assert "No space left" in err
    assert "Nothing was changed." in err
    assert path.read_bytes() == before  # and that's true


def test_config_set_still_works_on_a_good_file(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "config.toml"
    monkeypatch.setenv(cfg.ENV_VAR, str(path))
    assert cli_config.main(["set", "intake.token", "abc"]) == 0
    assert cfg.load(path).intake.token == "abc"


def test_udev_rules_on_a_broken_config_prints_no_rules(broken, monkeypatch) -> None:
    monkeypatch.setenv(cfg.ENV_VAR, str(broken))
    out: list[str] = []
    err: list[str] = []
    camera = camnames.CameraId(
        link="/dev/v4l/by-id/usb-Example_Cam-video-index0",
        bus="usb",
        serial="Example_Cam",
        label="Example",
    )
    code = camnames.udev_rules_main(
        out=out.append, err=err.append, platform="linux", cameras=lambda: [camera]
    )
    assert code == 1
    assert out == []  # no rules, rather than rules that drop the scope and thermal names
    assert str(broken) in err[0]
