"""Tests for the ``[chips]`` config, the source builder and the load report.

No Qt here — the Preferences tab has its own file. Every chip is made up, and
the folders are ``tmp_path`` ones or the built-index fixture.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

import pytest

from benchhud import config as cfg
from benchhud.chips import (
    Chip,
    CompositeChipSource,
    DirChipSource,
    InMemoryChipSource,
    NullChipSource,
)
from benchhud.chips import dir_source as dir_source_module
from benchhud.chips.builder import (
    KNOWN_KINDS,
    build_chip_source,
    left_out,
    name_key,
    probe_folder,
    warm,
)
from benchhud.chips.dir_source import FROM_FILES, FROM_INDEX, LoadReport
from benchhud.config import ChipsConfig, ChipSourceConfig, Config

FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

_CHIP_FILE = 'schema = 1\nmpn = "{mpn}"\nmanufacturer = "Example Semi"\n'


def _build_dir(root: Path) -> Path:
    """A folder holding the built-index fixture — an unpacked build."""
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, root / "index.json")
    return root


def _overlay(root: Path, *mpns: str) -> Path:
    """A folder of chip files and no vocab — a private overlay."""
    for mpn in mpns:
        path = root / "chips" / "example-semi" / f"{mpn}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_CHIP_FILE.format(mpn=mpn))
    return root


def _entry(name: str = "community", path: str = "/some/where", **fields) -> ChipSourceConfig:
    return ChipSourceConfig(name=name, path=path, **fields)


# --- the [chips] config ---------------------------------------------------


def test_no_chips_config_is_no_sources() -> None:
    assert Config().chips == ChipsConfig(datasheet_dir="", sources=[])
    assert Config.from_dict({}).chips.sources == []
    assert Config.from_dict({"chips": {}}).chips.sources == []


def test_sources_are_read_in_the_order_written() -> None:
    config = Config.from_dict(
        {
            "chips": {
                "datasheet_dir": "~/datasheets",
                "sources": [
                    {"name": "local", "kind": "dir", "path": "~/chips-private"},
                    {"name": "community", "path": "~/src/chip-data", "enabled": False},
                ],
            }
        }
    )
    assert config.chips.datasheet_dir == "~/datasheets"
    assert config.chips.sources == [
        ChipSourceConfig(name="local", kind="dir", path="~/chips-private", enabled=True),
        ChipSourceConfig(name="community", kind="dir", path="~/src/chip-data", enabled=False),
    ]


def test_a_kind_it_does_not_know_is_kept_not_dropped() -> None:
    # The plan's example: a later version's source, written into today's config.
    config = Config.from_dict(
        {"chips": {"sources": [{"name": "example-api", "kind": "http", "base_url": ""}]}}
    )
    assert config.chips.sources == [ChipSourceConfig(name="example-api", kind="http", path="")]


@pytest.mark.parametrize(
    "bad",
    [
        {"path": "~/no-name"},
        {"name": "", "path": "~/x"},
        {"name": "   ", "path": "~/x"},
        {"name": 7, "path": "~/x"},
        "community",
        ["community", "~/x"],
        None,
    ],
)
def test_an_entry_that_cannot_be_a_source_is_dropped_and_the_rest_kept(bad) -> None:
    config = Config.from_dict(
        {
            "chips": {
                "sources": [{"name": "local", "path": "~/a"}, bad, {"name": "b", "path": "~/b"}]
            }
        }
    )
    assert [source.name for source in config.chips.sources] == ["local", "b"]


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ({"name": "a", "path": 7}, _entry("a", "")),
        ({"name": "a", "kind": 7, "path": "~/x"}, _entry("a", "~/x")),
        ({"name": "a", "kind": " ", "path": "~/x"}, _entry("a", "~/x")),
        ({"name": " a ", "kind": " dir ", "path": "~/x"}, _entry("a", "~/x")),
        ({"name": "a", "kind": "Dir", "path": "~/x"}, _entry("a", "~/x")),
        ({"name": "a", "path": "  ~/x  "}, _entry("a", "~/x")),
        ({"name": "a", "path": "~/x", "enabled": "no"}, _entry("a", "~/x")),
        ({"name": "a", "path": "~/x", "enabled": 0}, _entry("a", "~/x")),
        ({"name": "a", "path": "~/x", "enabled": False}, _entry("a", "~/x", enabled=False)),
    ],
)
def test_a_value_of_the_wrong_type_falls_back(written, read) -> None:
    assert Config.from_dict({"chips": {"sources": [written]}}).chips.sources == [read]


@pytest.mark.parametrize("chips", ["yes", 7, ["a"], {"sources": "community"}, {"sources": {}}])
def test_a_chips_table_of_the_wrong_shape_is_no_sources(chips) -> None:
    config = Config.from_dict({"chips": chips})
    assert config.chips == ChipsConfig()


def test_a_datasheet_dir_that_is_not_text_is_ignored() -> None:
    assert Config.from_dict({"chips": {"datasheet_dir": 7}}).chips.datasheet_dir == ""


def test_chips_survive_a_save_and_a_load(tmp_path) -> None:
    config = Config()
    config.chips.datasheet_dir = "~/datasheets"
    config.chips.sources = [
        _entry("local", "~/chips-private"),
        _entry("community", "~/src/chip-data", enabled=False),
        ChipSourceConfig(name="example-api", kind="http"),
    ]
    path = tmp_path / "config.toml"
    cfg.save(config, path)
    assert cfg.load(path) == config
    text = path.read_text()
    assert text.index('name = "local"') < text.index('name = "community"')
    assert "[[chips.sources]]" in text


def test_no_sources_saves_no_empty_list_to_trip_a_hand_edit(tmp_path) -> None:
    import tomllib

    config = Config()
    config.chips.datasheet_dir = "~/datasheets"
    path = cfg.save(config, tmp_path / "config.toml")
    text = path.read_text()
    assert "\nsources =" not in text  # no `sources = []` line
    assert "[chips]" in text
    assert cfg.load(path) == config
    # What config.example.toml tells people to add, appended below what was saved.
    path.write_text(text + '\n[[chips.sources]]\nname = "community"\npath = "~/src/chip-data"\n')
    tomllib.loads(path.read_text())  # no "cannot mutate" error
    assert cfg.load(path).chips.sources == [_entry("community", "~/src/chip-data")]
    assert cfg.load(path).chips.datasheet_dir == "~/datasheets"


# --- which entries become sources ------------------------------------------


def test_dir_is_the_only_kind_there_is() -> None:
    assert KNOWN_KINDS == ("dir",)


def test_an_entry_that_is_fine_is_not_left_out() -> None:
    assert left_out(_entry()) is None
    assert left_out(_entry(), taken={"local"}) is None


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        (_entry(enabled=False), "switched off"),
        (ChipSourceConfig(name="api", kind="http", path="x"), 'kind "http"'),
        (_entry(path=""), "no folder"),
        (_entry(path="   "), "no folder"),
    ],
)
def test_why_an_entry_is_left_out(entry, why: str) -> None:
    assert why in left_out(entry)


def test_a_name_already_taken_is_left_out_whatever_its_case() -> None:
    assert name_key(" Local ") == name_key("local")
    reason = left_out(_entry("Local"), taken={name_key("local")})
    assert '"Local" is already used above' in reason


def test_switched_off_is_the_first_reason_given() -> None:
    entry = ChipSourceConfig(name="api", kind="http", path="", enabled=False)
    assert left_out(entry, taken={"api"}) == "switched off"


# --- building the source ---------------------------------------------------


def test_no_sources_is_the_null_source() -> None:
    assert isinstance(build_chip_source([]), NullChipSource)
    assert isinstance(build_chip_source(Config().chips.sources), NullChipSource)


def test_nothing_usable_is_the_null_source_too(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.chips.builder")
    source = build_chip_source(
        [
            _entry("off", enabled=False),
            ChipSourceConfig(name="example-api", kind="http"),
            _entry("nowhere", path=""),
        ]
    )
    assert isinstance(source, NullChipSource)
    said = [record.getMessage() for record in caplog.records]
    assert len(said) == 2  # switched off is a choice, not a problem
    assert any("example-api" in line and 'kind "http"' in line for line in said)
    assert any("nowhere" in line and "no folder" in line for line in said)


def test_sources_come_out_in_the_configured_order(tmp_path) -> None:
    source = build_chip_source(
        [
            _entry("local", str(tmp_path / "a")),
            _entry("off", str(tmp_path / "b"), enabled=False),
            _entry("community", str(tmp_path / "c")),
        ]
    )
    assert isinstance(source, CompositeChipSource)
    assert [s.name for s in source.sources] == ["local", "community"]
    assert all(isinstance(s, DirChipSource) for s in source.sources)
    assert [s.root for s in source.sources] == [tmp_path / "a", tmp_path / "c"]


def test_one_source_is_still_a_composite(tmp_path) -> None:
    source = build_chip_source([_entry("community", str(_build_dir(tmp_path)))])
    assert isinstance(source, CompositeChipSource)
    assert source.merged("EX1234QFN").sources == ("community",)


def test_a_name_used_twice_keeps_the_first(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.chips.builder")
    source = build_chip_source(
        [_entry("community", str(tmp_path / "a")), _entry("Community", str(tmp_path / "b"))]
    )
    assert [s.root for s in source.sources] == [tmp_path / "a"]
    assert "already used above" in caplog.records[0].getMessage()


def test_a_name_freed_by_a_switched_off_entry_can_be_used(tmp_path) -> None:
    source = build_chip_source(
        [
            _entry("community", str(tmp_path / "old"), enabled=False),
            _entry("community", str(tmp_path / "new")),
        ]
    )
    assert [s.root for s in source.sources] == [tmp_path / "new"]


def test_a_padded_folder_is_read_the_same_as_the_tab_shows_it(tmp_path) -> None:
    source = build_chip_source([_entry("community", f"  {_build_dir(tmp_path)}  ")])
    assert source.get("EX1234QFN") is not None
    assert probe_folder(f"  {tmp_path}  ").chips == 4


def test_building_reads_nothing(tmp_path, monkeypatch) -> None:
    reads: list[object] = []
    real = dir_source_module._load
    monkeypatch.setattr(
        dir_source_module, "_load", lambda root, name: (reads.append(root), real(root, name))[1]
    )
    source = build_chip_source([_entry("community", str(_build_dir(tmp_path)))])
    assert reads == []
    assert source.get("EX1234QFN") is not None  # the first lookup is what reads
    assert reads == [tmp_path]


def test_the_built_source_puts_an_overlay_over_the_community_data(tmp_path) -> None:
    overlay = _overlay(tmp_path / "private", "EX1234QFN", "EXLOCAL1")
    source = build_chip_source(
        [_entry("local", str(overlay)), _entry("community", str(_build_dir(tmp_path / "build")))]
    )
    merged = source.merged("EX1234QFN")
    assert merged.sources == ("local", "community")
    assert merged.source_of("package") == "community"
    assert source.get("EXLOCAL1") == Chip(mpn="EXLOCAL1", manufacturer="Example Semi")
    assert [m.chip.mpn for m in source.by_marking("X1=6A")] == ["EX1234QFN", "EX9000SOT"]


# --- reading ahead of the first lookup ------------------------------------


def test_the_null_source_has_nothing_to_warm_up() -> None:
    ran: list[object] = []
    assert warm(NullChipSource(), ran.append) is False
    assert warm(InMemoryChipSource(), ran.append) is False
    assert ran == []


def test_warming_hands_the_read_to_the_runner(tmp_path, monkeypatch) -> None:
    reads: list[object] = []
    real = dir_source_module._load
    monkeypatch.setattr(
        dir_source_module, "_load", lambda root, name: (reads.append(root), real(root, name))[1]
    )
    source = build_chip_source([_entry("community", str(_build_dir(tmp_path)))])
    jobs: list = []
    assert warm(source, jobs.append) is True
    assert reads == []  # handed over, not run here
    jobs[0]()
    assert reads == [tmp_path]
    assert source.get("EX1234QFN") is not None
    assert reads == [tmp_path]  # already in memory: the lookup read nothing


def test_warming_runs_on_another_thread_by_default(tmp_path, monkeypatch) -> None:
    where: list[str] = []
    done = threading.Event()
    real = dir_source_module._load

    def load(root, name):
        where.append(threading.current_thread().name)
        try:
            return real(root, name)
        finally:
            done.set()

    monkeypatch.setattr(dir_source_module, "_load", load)
    source = build_chip_source([_entry("community", str(_build_dir(tmp_path)))])
    assert warm(source) is True
    assert done.wait(timeout=10)
    assert where == ["chips-load"]
    assert threading.current_thread().name != "chips-load"


def test_reloading_a_composite_reloads_every_source_that_can(tmp_path) -> None:
    class Stuck:
        name = "stuck"

        def get(self, mpn):
            return None

        def by_marking(self, text):
            return []

        def reload(self):
            raise RuntimeError("reload fell over")

    folder = _overlay(tmp_path, "EX1")
    reader = DirChipSource(root=folder, name="local")
    source = CompositeChipSource([Stuck(), InMemoryChipSource(), reader])
    assert source.get("EX2") is None
    _overlay(tmp_path, "EX2")
    source.reload()  # one failing, one with nothing to reload, one that reads
    assert source.get("EX2") is not None


# --- what a read came to ---------------------------------------------------


def test_a_report_for_a_built_index() -> None:
    report = DirChipSource(root=FIXTURE).report()
    assert report == LoadReport(chips=4, read_from=FROM_INDEX, origin=FIXTURE, problems=())


def test_a_report_for_chip_files(tmp_path) -> None:
    report = DirChipSource(root=_overlay(tmp_path, "EX1", "EX2")).report()
    assert (report.chips, report.read_from, report.origin) == (2, FROM_FILES, tmp_path / "chips")
    assert report.problems == ()


def test_a_report_says_why_nothing_loaded(tmp_path) -> None:
    report = DirChipSource(root=tmp_path / "nowhere", name="community").report()
    assert (report.chips, report.read_from, report.origin) == (0, "", None)
    (why,) = report.problems
    assert "is not a directory this can read" in why
    # The log line names the source; the report is already about one.
    assert why.startswith(str(tmp_path / "nowhere"))

    (tmp_path / "empty").mkdir()
    (why,) = DirChipSource(root=tmp_path / "empty", name="odd: name").report().problems
    assert why == f"no index.json and no chips/ in {tmp_path / 'empty'}"


def test_the_log_line_still_names_the_source(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="benchhud.chips.dir_source")
    DirChipSource(root=tmp_path / "nowhere", name="community").reload()
    (line,) = [record.getMessage() for record in caplog.records]
    assert (
        line
        == f"chips: source 'community': {tmp_path / 'nowhere'} is not a directory this can read"
    )


def test_a_report_lists_what_was_skipped_along_the_way(tmp_path) -> None:
    folder = _overlay(tmp_path, "EX1", "EX2")
    (folder / "chips" / "example-semi" / "EXBAD.toml").write_text("mpn = [unclosed")
    (folder / "chips" / "example-semi" / "EXOLD.toml").write_text(
        'schema = 9\nmpn = "EXOLD"\nmanufacturer = "Example Semi"\n'
    )
    report = DirChipSource(root=folder).report()
    assert report.chips == 2
    assert len(report.problems) == 2
    assert "EXBAD.toml" in report.problems[0]
    assert "EXOLD.toml" in report.problems[1]


def test_a_report_says_when_a_stale_index_was_passed_over(tmp_path) -> None:
    import os

    folder = _overlay(tmp_path, "EX1")
    (folder / "out").mkdir()
    shutil.copy(FIXTURE, folder / "out" / "index.json")
    os.utime(folder / "out" / "index.json", (1000.0, 1000.0))
    report = DirChipSource(root=folder).report()
    assert (report.chips, report.read_from) == (1, FROM_FILES)
    (note,) = report.problems
    assert "may be out of date" in note


def test_a_report_for_a_format_it_does_not_read(tmp_path) -> None:
    (tmp_path / "index.json").write_text('{"format": 2, "chips": []}')
    report = DirChipSource(root=tmp_path).report()
    assert (report.chips, report.read_from) == (0, "")
    assert "format 2" in report.problems[0]


def test_a_long_run_of_problems_is_cut_short(tmp_path) -> None:
    folder = _overlay(tmp_path, "EX1")
    for number in range(150):
        (folder / "chips" / "example-semi" / f"BAD{number:03}.toml").write_text("x = [")
    report = DirChipSource(root=folder).report()
    assert report.chips == 1
    assert len(report.problems) == 101
    assert report.problems[-1] == "… and 50 more; the log has them all"


def test_a_report_is_for_the_latest_read_only(tmp_path) -> None:
    folder = _overlay(tmp_path, "EX1")
    bad = folder / "chips" / "example-semi" / "EXBAD.toml"
    bad.write_text("x = [")
    source = DirChipSource(root=folder)
    assert len(source.report().problems) == 1
    bad.unlink()
    source.reload()
    assert source.report().problems == ()
    # And one source's problems aren't another's.
    assert DirChipSource(root=FIXTURE).report().problems == ()


def test_a_failure_nobody_planned_for_is_in_the_report(tmp_path, monkeypatch) -> None:
    def broken(root, name):
        raise RuntimeError("something new")

    monkeypatch.setattr(dir_source_module, "_load", broken)
    report = DirChipSource(root=tmp_path).report()
    assert report.chips == 0
    assert "something new" in report.problems[0]


def test_problems_from_two_reads_at_once_do_not_mix(tmp_path) -> None:
    good = _overlay(tmp_path / "good", "EX1")
    bad = _overlay(tmp_path / "bad", "EX2")
    for number in range(40):
        (bad / "chips" / "example-semi" / f"BAD{number}.toml").write_text("x = [")
    sources = [DirChipSource(root=good), DirChipSource(root=bad)] * 4
    start = threading.Barrier(len(sources))
    reports: list[tuple[Path, LoadReport]] = []

    def read(source: DirChipSource) -> None:
        start.wait(timeout=10)
        reports.append((source.root, DirChipSource(root=source.root).report()))

    threads = [threading.Thread(target=read, args=(source,)) for source in sources]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(reports) == 8
    for root, report in reports:
        assert len(report.problems) == (0 if root == good else 40)


def test_probing_a_folder_reports_without_keeping_anything(tmp_path) -> None:
    report = probe_folder(str(_build_dir(tmp_path)))
    assert (report.chips, report.read_from) == (4, FROM_INDEX)
    assert probe_folder(str(tmp_path / "nowhere")).chips == 0
    assert probe_folder("").problems == ()  # nothing configured, nothing wrong
