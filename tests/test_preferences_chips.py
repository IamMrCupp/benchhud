"""Tests for the Chips tab in Preferences, and the main window's chip source.

Hardware-free and offscreen. The tab reads folders on a worker; here the
runner is a stand-in that runs the job on the spot (or holds it), and the
probe is a fake unless a test says otherwise. Every chip is made up.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QTabWidget

from benchhud.chips import CompositeChipSource, NullChipSource, builder
from benchhud.chips import dir_source as dir_source_module
from benchhud.chips.builder import probe_folder
from benchhud.chips.dir_source import FROM_FILES, FROM_INDEX, LoadReport
from benchhud.config import ChipSourceConfig, Config
from benchhud.shell import main_window as main_window_module
from benchhud.shell.main_window import MainWindow
from benchhud.shell.preferences import PreferencesDialog, describe_load

FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

NAME, FOLDER, ON, ON_AIR, LOADED = range(5)


class _FakeScanner:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


class _Probe:
    """A stand-in for reading a folder: canned answers, and a record of what was asked."""

    def __init__(self, **reports: LoadReport) -> None:
        self.reports = reports
        self.asked: list[str] = []

    def __call__(self, folder: str) -> LoadReport:
        self.asked.append(folder)
        return self.reports.get(folder, LoadReport(problems=(f"{folder} is not there",)))


class _Held:
    """A runner that keeps the jobs it's given until told to run them."""

    def __init__(self) -> None:
        self.jobs: list = []

    def __call__(self, job) -> None:
        self.jobs.append(job)

    def run(self) -> None:
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


def _now(job) -> None:
    job()


_INDEX = LoadReport(chips=412, read_from=FROM_INDEX, origin=Path("/data/out/index.json"))
_FILES = LoadReport(chips=3, read_from=FROM_FILES, origin=Path("/private/chips"))


def _config(*sources: ChipSourceConfig) -> Config:
    config = Config()
    config.chips.sources = list(sources)
    return config


def _two() -> Config:
    return _config(
        ChipSourceConfig(name="local", path="/private"),
        ChipSourceConfig(name="community", path="/data"),
    )


def _dialog(qtbot, config: Config, probe=None, run=_now, **kwargs) -> PreferencesDialog:
    probe = probe if probe is not None else _Probe(**{"/private": _FILES, "/data": _INDEX})
    dialog = PreferencesDialog(
        config, _FakeScanner(), chip_probe=probe, run_in_background=run, **kwargs
    )
    qtbot.addWidget(dialog)
    return dialog


def _column(dialog: PreferencesDialog, column: int) -> list[str]:
    table = dialog.chips_table
    return [table.item(row, column).text() for row in range(table.rowCount())]


def _ticked(dialog: PreferencesDialog) -> list[bool]:
    table = dialog.chips_table
    return [
        table.item(row, ON).checkState() == Qt.CheckState.Checked for row in range(table.rowCount())
    ]


# --- the tab --------------------------------------------------------------


def test_there_is_a_chips_tab(qtbot) -> None:
    dialog = _dialog(qtbot, Config())
    tabs = dialog.findChild(QTabWidget)
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    assert "Chips" in titles
    assert tabs.widget(titles.index("Chips")).isAncestorOf(dialog.chips_table)


def test_no_sources_is_an_empty_table_and_reads_nothing(qtbot) -> None:
    probe = _Probe()
    dialog = _dialog(qtbot, Config(), probe=probe)
    assert dialog.chips_table.rowCount() == 0
    assert probe.asked == []


def test_the_table_lists_the_sources_in_order(qtbot) -> None:
    config = _two()
    config.chips.sources[1].enabled = False
    dialog = _dialog(qtbot, config)
    assert _column(dialog, NAME) == ["local", "community"]
    assert _column(dialog, FOLDER) == ["/private", "/data"]
    assert _ticked(dialog) == [True, False]
    headers = [dialog.chips_table.horizontalHeaderItem(c).text() for c in range(5)]
    assert headers == ["Name", "Folder", "On", "On air", "Loaded"]


def test_the_tab_says_the_order_is_precedence(qtbot) -> None:
    dialog = _dialog(qtbot, _two())
    assert "higher up wins" in dialog.chips_order_note.text()


# --- what each source loaded ----------------------------------------------


def test_each_source_shows_what_it_loaded(qtbot) -> None:
    dialog = _dialog(qtbot, _two())
    assert _column(dialog, LOADED) == [
        "3 chips, from the chip files",
        "412 chips, from the built index",
    ]
    assert dialog.chips_table.item(1, LOADED).toolTip() == str(Path("/data/out/index.json"))


def test_a_source_that_loaded_nothing_says_why(qtbot) -> None:
    probe = _Probe(**{"/private": LoadReport(problems=("/private is not a directory",))})
    dialog = _dialog(qtbot, _config(ChipSourceConfig(name="local", path="/private")), probe=probe)
    assert _column(dialog, LOADED) == ["Nothing loaded: /private is not a directory"]


def test_a_source_left_out_says_why_and_is_not_read(qtbot) -> None:
    probe = _Probe(**{"/data": _INDEX})
    config = _config(
        ChipSourceConfig(name="community", path="/data"),
        ChipSourceConfig(name="off", path="/off", enabled=False),
        ChipSourceConfig(name="example-api", kind="http"),
        ChipSourceConfig(name="blank", path=""),
        ChipSourceConfig(name="Community", path="/again"),
    )
    dialog = _dialog(qtbot, config, probe=probe)
    assert _column(dialog, LOADED) == [
        "412 chips, from the built index",
        "Left out: switched off",
        'Left out: kind "http" isn\'t one this benchhud reads',
        "Left out: no folder set",
        'Left out: the name "Community" is already used above',
    ]
    assert probe.asked == ["/data"]


def test_a_folder_is_being_checked_until_its_answer_comes_back(qtbot) -> None:
    held = _Held()
    dialog = _dialog(qtbot, _two(), run=held)
    assert _column(dialog, LOADED) == ["Checking…", "Checking…"]
    assert len(held.jobs) == 2
    held.run()
    assert _column(dialog, LOADED) == [
        "3 chips, from the chip files",
        "412 chips, from the built index",
    ]


def test_a_folder_is_read_once_however_often_the_table_changes(qtbot) -> None:
    probe = _Probe(**{"/private": _FILES, "/data": _INDEX})
    held = _Held()
    dialog = _dialog(qtbot, _two(), probe=probe, run=held)
    dialog.move_chip_source(1)  # redraws while both are still being read
    dialog.chips_table.item(0, NAME).setText("renamed")
    assert len(held.jobs) == 2
    held.run()
    dialog.chips_table.item(0, NAME).setText("again")
    assert sorted(probe.asked) == ["/data", "/private"]
    assert held.jobs == []


def test_two_sources_on_one_folder_are_one_read(qtbot) -> None:
    probe = _Probe(**{"/data": _INDEX})
    config = _config(
        ChipSourceConfig(name="a", path="/data"), ChipSourceConfig(name="b", path="/data")
    )
    dialog = _dialog(qtbot, config, probe=probe)
    assert probe.asked == ["/data"]
    assert _column(dialog, LOADED) == ["412 chips, from the built index"] * 2


def test_editing_a_folder_reads_the_new_one(qtbot) -> None:
    probe = _Probe(**{"/data": _INDEX, "/elsewhere": _FILES})
    dialog = _dialog(qtbot, _config(ChipSourceConfig(name="c", path="/data")), probe=probe)
    dialog.chips_table.item(0, FOLDER).setText("/elsewhere")
    assert _column(dialog, LOADED) == ["3 chips, from the chip files"]
    assert probe.asked == ["/data", "/elsewhere"]


def test_unticking_a_source_leaves_it_out_and_ticking_brings_it_back(qtbot) -> None:
    dialog = _dialog(qtbot, _two())
    dialog.chips_table.item(0, ON).setCheckState(Qt.CheckState.Unchecked)
    assert _column(dialog, LOADED)[0] == "Left out: switched off"
    dialog.chips_table.item(0, ON).setCheckState(Qt.CheckState.Checked)
    assert _column(dialog, LOADED)[0] == "3 chips, from the chip files"


def test_check_again_reads_every_folder_again(qtbot) -> None:
    probe = _Probe(**{"/private": _FILES, "/data": _INDEX})
    dialog = _dialog(qtbot, _two(), probe=probe)
    probe.reports["/data"] = LoadReport(chips=413, read_from=FROM_INDEX, origin=Path("/data"))
    dialog.check_chip_sources_button.click()
    assert probe.asked == ["/private", "/data", "/private", "/data"]
    assert _column(dialog, LOADED)[1] == "413 chips, from the built index"


def test_a_probe_that_raises_shows_as_nothing_loaded(qtbot) -> None:
    def broken(folder: str) -> LoadReport:
        raise RuntimeError("disk on fire")

    dialog = _dialog(qtbot, _config(ChipSourceConfig(name="c", path="/data")), probe=broken)
    (status,) = _column(dialog, LOADED)
    assert status.startswith("Nothing loaded:")
    assert "disk on fire" in status


def test_an_answer_that_arrives_after_the_dialog_is_gone_is_dropped(qtbot) -> None:
    held = _Held()
    dialog = PreferencesDialog(
        _two(), _FakeScanner(), chip_probe=_Probe(**{"/data": _INDEX}), run_in_background=held
    )
    assert len(held.jobs) == 2
    dialog.deleteLater()
    qtbot.wait(50)  # let Qt delete it
    held.run()  # must not raise


def test_folders_are_read_off_the_gui_thread_for_real(qtbot, tmp_path) -> None:
    # The real runner and the real reading, against a real folder.
    build = tmp_path / "build"
    build.mkdir()
    shutil.copy(FIXTURE, build / "index.json")
    config = _config(
        ChipSourceConfig(name="community", path=str(build)),
        ChipSourceConfig(name="gone", path=str(tmp_path / "nowhere")),
    )
    read_on: list[threading.Thread] = []

    def probe(folder: str) -> LoadReport:
        read_on.append(threading.current_thread())
        return probe_folder(folder)

    dialog = PreferencesDialog(config, _FakeScanner(), chip_probe=probe)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: "Checking…" not in _column(dialog, LOADED), timeout=10_000)
    loaded = _column(dialog, LOADED)
    assert loaded[0] == "4 chips, from the built index"
    assert loaded[1].startswith("Nothing loaded:")
    assert "is not a directory this can read" in loaded[1]
    assert len(read_on) == 2
    assert all(thread is not threading.main_thread() for thread in read_on)
    assert {thread.name for thread in read_on} == {"chips-check"}


def test_a_name_freed_by_a_source_that_is_left_out_can_be_used(qtbot) -> None:
    config = _config(
        ChipSourceConfig(name="community", path="/private", enabled=False),
        ChipSourceConfig(name="community", path="/data"),
    )
    dialog = _dialog(qtbot, config)
    assert _column(dialog, LOADED) == [
        "Left out: switched off",
        "412 chips, from the built index",
    ]


def test_a_nameless_row_is_judged_under_the_name_it_will_be_saved_with(qtbot) -> None:
    probe = _Probe(**{"/a/chips": _FILES, "/b/chips": _INDEX})
    dialog = _dialog(qtbot, _config(ChipSourceConfig(name="chips", path="/a/chips")), probe=probe)
    dialog.add_chip_source_button.click()
    dialog.chips_table.item(1, FOLDER).setText("/b/chips")  # no name: it'll be "chips" too
    assert _column(dialog, LOADED) == [
        "3 chips, from the chip files",
        'Left out: the name "chips" is already used above',
    ]
    assert probe.asked == ["/a/chips"]
    dialog.apply_to_config()
    assert [source.name for source in dialog._config.chips.sources] == ["chips", "chips"]


def test_check_again_does_not_take_an_answer_from_before_the_click(qtbot) -> None:
    probe = _Probe(**{"/data": _INDEX})
    held = _Held()
    dialog = _dialog(
        qtbot, _config(ChipSourceConfig(name="c", path="/data")), probe=probe, run=held
    )
    before, held.jobs = held.jobs, []
    assert len(before) == 1

    probe.reports["/data"] = LoadReport(chips=413, read_from=FROM_INDEX, origin=Path("/data"))
    dialog.check_chip_sources_button.click()
    assert len(held.jobs) == 1  # read afresh, not waiting on the old read

    probe.reports["/data"] = _INDEX  # what the old read would have seen
    before[0]()
    assert _column(dialog, LOADED) == ["Checking…"]  # the old answer is dropped
    probe.reports["/data"] = LoadReport(chips=413, read_from=FROM_INDEX, origin=Path("/data"))
    held.run()
    assert _column(dialog, LOADED) == ["413 chips, from the built index"]


def test_an_answer_that_is_not_a_report_is_nothing_loaded_and_asked_once(qtbot) -> None:
    asked: list[str] = []

    def probe(folder: str):
        asked.append(folder)
        return "fine"

    dialog = _dialog(qtbot, _config(ChipSourceConfig(name="c", path="/data")), probe=probe)
    assert _column(dialog, LOADED) == ["Nothing loaded: reading /data gave no answer"]
    assert asked == ["/data"]


def test_no_worker_to_be_had_is_said_in_the_row_and_can_be_tried_again(qtbot) -> None:
    probe = _Probe(**{"/data": _INDEX})
    workers = {"available": False}

    def run(job) -> None:
        if not workers["available"]:
            raise RuntimeError("can't start new thread")
        job()

    dialog = _dialog(qtbot, _config(ChipSourceConfig(name="c", path="/data")), probe=probe, run=run)
    (status,) = _column(dialog, LOADED)
    assert status == "Nothing loaded: couldn't start reading /data: can't start new thread"
    assert probe.asked == []
    workers["available"] = True
    dialog.check_chip_sources_button.click()
    assert _column(dialog, LOADED) == ["412 chips, from the built index"]


@pytest.mark.parametrize(
    ("report", "text", "tip"),
    [
        (_INDEX, "412 chips, from the built index", str(Path("/data/out/index.json"))),
        (
            LoadReport(chips=1, read_from=FROM_FILES, origin=Path("/p/chips")),
            "1 chip, from the chip files",
            str(Path("/p/chips")),
        ),
        (
            LoadReport(chips=0, read_from=FROM_FILES, origin=Path("/p/chips")),
            "0 chips, from the chip files",
            str(Path("/p/chips")),
        ),
        (
            LoadReport(chips=2, read_from=FROM_FILES, origin=Path("/p/chips"), problems=("a",)),
            "2 chips, from the chip files — 1 note",
            f"{Path('/p/chips')}\na",
        ),
        (
            LoadReport(
                chips=2, read_from=FROM_INDEX, origin=Path("/p/index.json"), problems=("a", "b")
            ),
            "2 chips, from the built index — 2 notes",
            f"{Path('/p/index.json')}\na\nb",
        ),
        (LoadReport(problems=("no folder", "x")), "Nothing loaded: no folder", "no folder\nx"),
        (LoadReport(), "Nothing loaded: nothing there to read", ""),
    ],
)
def test_what_a_load_reads_as(report: LoadReport, text: str, tip: str) -> None:
    assert describe_load(report) == (text, tip)


# --- add, remove, reorder, browse ------------------------------------------


def test_add_puts_an_empty_source_at_the_bottom(qtbot) -> None:
    dialog = _dialog(qtbot, _two())
    dialog.add_chip_source_button.click()
    assert _column(dialog, NAME) == ["local", "community", ""]
    assert _ticked(dialog)[-1] is True
    assert dialog.chips_table.currentRow() == 2
    assert _column(dialog, LOADED)[-1] == "Left out: no folder set"


def test_remove_takes_out_the_selected_source(qtbot) -> None:
    dialog = _dialog(qtbot, _two())
    dialog.chips_table.setCurrentCell(0, NAME)
    dialog.remove_chip_source_button.click()
    assert _column(dialog, NAME) == ["community"]
    dialog.remove_chip_source_button.click()
    assert dialog.chips_table.rowCount() == 0
    dialog.remove_chip_source_button.click()  # nothing left: nothing happens


def test_remove_with_nothing_selected_does_nothing(qtbot) -> None:
    dialog = _dialog(qtbot, _two())
    dialog.chips_table.setCurrentCell(-1, -1)
    dialog.remove_chip_source_button.click()
    assert _column(dialog, NAME) == ["local", "community"]


def test_move_up_and_down_change_the_order(qtbot) -> None:
    config = _two()
    config.chips.sources[0].enabled = False
    dialog = _dialog(qtbot, config)
    dialog.chips_table.setCurrentCell(1, NAME)
    dialog.chip_source_up_button.click()
    assert _column(dialog, NAME) == ["community", "local"]
    assert _column(dialog, FOLDER) == ["/data", "/private"]
    assert _ticked(dialog) == [True, False]  # the row moves whole
    assert dialog.chips_table.currentRow() == 0  # and stays selected
    dialog.chip_source_up_button.click()  # already at the top
    assert _column(dialog, NAME) == ["community", "local"]
    dialog.chip_source_down_button.click()
    assert _column(dialog, NAME) == ["local", "community"]
    dialog.chip_source_down_button.click()  # already at the bottom
    assert _column(dialog, NAME) == ["local", "community"]


def test_reordering_changes_who_owns_a_name(qtbot) -> None:
    config = _config(
        ChipSourceConfig(name="community", path="/data"),
        ChipSourceConfig(name="community", path="/private"),
    )
    dialog = _dialog(qtbot, config)
    assert _column(dialog, LOADED)[1].startswith("Left out: the name")
    dialog.chips_table.setCurrentCell(1, NAME)
    dialog.chip_source_up_button.click()
    assert _column(dialog, LOADED) == [
        "3 chips, from the chip files",
        'Left out: the name "community" is already used above',
    ]


def test_browse_sets_the_selected_source_s_folder(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "/data")
    dialog = _dialog(qtbot, _config(ChipSourceConfig(name="community", path="/old")))
    dialog.chips_table.setCurrentCell(0, NAME)
    dialog.browse_chip_folder_button.click()
    assert _column(dialog, FOLDER) == ["/data"]
    assert _column(dialog, NAME) == ["community"]  # a name already given is kept
    assert _column(dialog, LOADED) == ["412 chips, from the built index"]


def test_browse_names_a_nameless_source_after_its_folder(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "/src/chip-data")
    dialog = _dialog(qtbot, Config())
    dialog.add_chip_source_button.click()
    dialog.browse_chip_folder_button.click()
    assert _column(dialog, NAME) == ["chip-data"]
    assert _column(dialog, FOLDER) == ["/src/chip-data"]


def test_browse_with_no_source_selected_adds_one(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "/src/chip-data")
    dialog = _dialog(qtbot, Config())
    dialog.browse_chip_folder_button.click()
    assert _column(dialog, NAME) == ["chip-data"]
    assert _ticked(dialog) == [True]


def test_browse_cancelled_changes_nothing(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    dialog = _dialog(qtbot, _two())
    dialog.browse_chip_folder_button.click()
    assert _column(dialog, FOLDER) == ["/private", "/data"]


# --- saving ---------------------------------------------------------------


def test_ok_writes_the_sources_back_in_the_order_shown(qtbot) -> None:
    saved: list[Config] = []
    config = _two()
    config.chips.datasheet_dir = "~/datasheets"
    dialog = _dialog(qtbot, config, on_save=saved.append)
    dialog.chips_table.setCurrentCell(1, NAME)
    dialog.chip_source_up_button.click()
    dialog.chips_table.item(1, ON).setCheckState(Qt.CheckState.Unchecked)
    dialog.chips_table.item(0, FOLDER).setText("~/src/chip-data")
    dialog.accept()
    assert saved == [config]
    assert config.chips.sources == [
        ChipSourceConfig(name="community", kind="dir", path="~/src/chip-data", enabled=True),
        ChipSourceConfig(name="local", kind="dir", path="/private", enabled=False),
    ]
    assert config.chips.datasheet_dir == "~/datasheets"  # shown, and saved back as it was


def test_a_kind_this_version_does_not_read_is_saved_back_as_it_was(qtbot) -> None:
    config = _config(
        ChipSourceConfig(name="example-api", kind="http", enabled=False),
        ChipSourceConfig(name="community", path="/data"),
    )
    dialog = _dialog(qtbot, config)
    dialog.chips_table.setCurrentCell(0, NAME)
    dialog.chip_source_down_button.click()
    dialog.apply_to_config()
    assert config.chips.sources == [
        ChipSourceConfig(name="community", path="/data"),
        ChipSourceConfig(name="example-api", kind="http", path="", enabled=False),
    ]


def test_a_row_left_empty_is_not_saved_and_a_nameless_one_gets_a_name(qtbot) -> None:
    dialog = _dialog(qtbot, Config())
    for _ in range(3):
        dialog.add_chip_source_button.click()
    dialog.chips_table.item(0, FOLDER).setText("/src/chip-data")
    dialog.chips_table.item(2, NAME).setText("  later  ")
    dialog.apply_to_config()
    assert dialog._config.chips.sources == [
        ChipSourceConfig(name="chip-data", path="/src/chip-data"),
        ChipSourceConfig(name="later", path=""),
    ]


def test_cancel_changes_nothing(qtbot) -> None:
    config = _two()
    dialog = _dialog(qtbot, config)
    dialog.add_chip_source_button.click()
    dialog.chips_table.item(0, NAME).setText("renamed")
    dialog.reject()
    assert [source.name for source in config.chips.sources] == ["local", "community"]


def test_what_is_saved_loads_back_the_same(qtbot, tmp_path) -> None:
    from benchhud import config as cfg

    config = _two()
    dialog = _dialog(qtbot, config)
    dialog.add_chip_source_button.click()
    dialog.chips_table.item(2, NAME).setText("third")
    dialog.chips_table.item(2, FOLDER).setText("~/third")
    dialog.apply_to_config()
    path = cfg.save(config, tmp_path / "config.toml")
    assert cfg.load(path).chips == config.chips
    assert [s.name for s in cfg.load(path).chips.sources] == ["local", "community", "third"]


# --- the main window's chip source -----------------------------------------


def _build(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    build.mkdir()
    shutil.copy(FIXTURE, build / "index.json")
    return build


@pytest.fixture
def worker_jobs(monkeypatch) -> list:
    """Jobs the chip source hands to its worker thread, held instead of run."""
    jobs: list = []
    monkeypatch.setattr(builder, "_thread", jobs.append)
    return jobs


@pytest.fixture
def folder_reads(monkeypatch) -> list:
    """Every directory read, as it happens."""
    reads: list = []
    real = dir_source_module._load
    monkeypatch.setattr(
        dir_source_module, "_load", lambda root, name: (reads.append(root), real(root, name))[1]
    )
    return reads


def test_with_no_sources_the_window_has_the_null_source_and_starts_no_thread(
    qtbot, worker_jobs, folder_reads
) -> None:
    window = MainWindow(scanner=_FakeScanner())
    qtbot.addWidget(window)
    assert isinstance(window.chip_source, NullChipSource)
    assert worker_jobs == []
    assert folder_reads == []


def test_configured_sources_are_built_and_read_off_the_gui_thread(
    qtbot, tmp_path, worker_jobs, folder_reads
) -> None:
    build = _build(tmp_path)
    window = MainWindow(
        _config(ChipSourceConfig(name="community", path=str(build))), scanner=_FakeScanner()
    )
    qtbot.addWidget(window)
    source = window.chip_source
    assert isinstance(source, CompositeChipSource)
    assert [s.name for s in source.sources] == ["community"]
    # Built, and the read handed to the worker — not done here.
    assert len(worker_jobs) == 1
    assert folder_reads == []
    worker_jobs.pop()()
    assert folder_reads == [build]
    assert source.get("EX1234QFN").package == "QFN-20 (3x3)"
    assert folder_reads == [build]  # already in memory: the lookup read nothing


def test_ok_with_the_sources_unchanged_keeps_the_source_and_reads_it_again(
    qtbot, tmp_path, worker_jobs, folder_reads
) -> None:
    build = _build(tmp_path)
    window = MainWindow(
        _config(ChipSourceConfig(name="community", path=str(build))),
        scanner=_FakeScanner(),
        on_persist=lambda config: None,
    )
    qtbot.addWidget(window)
    source = window.chip_source
    worker_jobs.pop()()

    dialog = window.open_preferences()
    # The tab reads the folder itself, on its own worker; let that finish so
    # it isn't counted below.
    qtbot.waitUntil(lambda: "Checking…" not in _column(dialog, LOADED), timeout=10_000)
    before = len(folder_reads)
    dialog.accept()  # nothing about chips changed
    assert window.chip_source is source  # the copy it holds keeps answering
    assert len(worker_jobs) == 1  # and it's read again, on the worker
    assert source.get("EX1234QFN") is not None
    assert len(folder_reads) == before  # not on this thread
    worker_jobs.pop()()
    assert folder_reads[before:] == [build]


def test_ok_in_preferences_rebuilds_the_chip_source(qtbot, monkeypatch, tmp_path) -> None:
    warmed: list[object] = []
    monkeypatch.setattr(main_window_module, "warm_chip_source", warmed.append)
    build = _build(tmp_path)
    window = MainWindow(scanner=_FakeScanner(), on_persist=lambda config: None)
    qtbot.addWidget(window)
    assert isinstance(window.chip_source, NullChipSource)

    dialog = window.open_preferences()
    dialog.add_chip_source_button.click()
    dialog.chips_table.item(0, NAME).setText("community")
    dialog.chips_table.item(0, FOLDER).setText(str(build))
    dialog.accept()

    source = window.chip_source
    assert isinstance(source, CompositeChipSource)
    assert warmed[-1] is source
    assert source.get("EX1234QFN") is not None
    assert window._config.chips.sources == [ChipSourceConfig(name="community", path=str(build))]
