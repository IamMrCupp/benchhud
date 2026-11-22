"""Chip details and Open datasheet on the inventory row, and the app's datasheet library.

Offscreen, no hardware, no network. Every part and every chip is made up —
"Example Semi" doesn't exist — and the datasheets are a few bytes in ``tmp_path``.
The panel looks things up on a worker; here the runner is a stand-in that runs
the job on the spot, or holds it so a test can change the selection first.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog

from benchhud.chips import (
    Chip,
    CompositeChipSource,
    DirChipSource,
    Document,
    InMemoryChipSource,
    Marking,
    NullChipSource,
    UsedIn,
    builder,
)
from benchhud.chips.builder import build_datasheet_library, warm_library
from benchhud.chips.datasheets import DatasheetFile, DatasheetLibrary, MatchedBy
from benchhud.config import ChipSourceConfig, Config
from benchhud.inventory import InMemoryInventorySource, Part
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.inventory import InventoryPanel, chip_summary, datasheet_status
from benchhud.shell.preferences import PreferencesDialog

FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

_REV_A = b"%PDF-1.4 EX1234 datasheet, revision A"
_REV_C = b"%PDF-1.4 EX1234 datasheet, revision C"

_BUCK = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    package="QFN-20 (3x3)",
    package_family="QFN",
    functions=("buck-controller", "load-switch"),
    description="Single-phase buck controller for a standby rail",
    markings=(Marking(code="X1="), Marking(code="X9")),
    documents=(
        Document(
            kind="datasheet", filename="EX1234.pdf", sha256=(hashlib.sha256(_REV_A).hexdigest(),)
        ),
    ),
    used_in=(UsedIn(board="EXAMPLE-BOARD-1", designator="U7000", role="standby 3.3 V"),),
)
_LDO = Chip(mpn="EX9000SOT", manufacturer="Example Semi", package="SOT-23-5", functions=("ldo",))
_BARE = Chip(mpn="EX0001", manufacturer="Example Semi")

_PARTS = [
    Part(id=1, description="Buck controller", mpn="EX1234QFN", status="low", quantity_on_hand=2),
    Part(id=2, description="LDO", mpn="EX9000SOT", status="low", quantity_on_hand=1),
    Part(id=3, description="Kapton tape", status="low", quantity_on_hand=0),  # no MPN
    Part(id=4, description="Mystery IC", mpn="ZZ9999", status="low", quantity_on_hand=1),
    Part(id=5, description="Bare", mpn="EX0001", status="low", quantity_on_hand=1),
]


class _FakeScanner:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


class _Held:
    """A runner that keeps the jobs it's given until told to run them."""

    def __init__(self) -> None:
        self.jobs: list = []

    def __call__(self, job) -> None:
        self.jobs.append(job)

    def run_next(self) -> None:
        self.jobs.pop(0)()


def _now(job) -> None:
    job()


def _library(tmp_path: Path, **files: bytes) -> DatasheetLibrary:
    folder = tmp_path / "datasheets"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_bytes(content)
    return DatasheetLibrary(folder)


def _panel(qtbot, *, chips=None, library=None, run=_now, parts=_PARTS, **kwargs) -> InventoryPanel:
    inventory = InMemoryInventorySource(list(parts))
    if chips is None:
        chips = InMemoryChipSource([_BUCK, _LDO, _BARE], "community")
    panel = InventoryPanel(
        lambda: inventory,
        chips=(lambda: chips) if chips is not False else None,
        datasheets=(lambda: library) if library is not None else None,
        run_in_background=run,
        **kwargs,
    )
    qtbot.addWidget(panel)
    panel.show()
    return panel


def _select(panel: InventoryPanel, mpn_or_text: str) -> None:
    for row in range(panel.part_list.count()):
        if mpn_or_text in panel.part_list.item(row).text():
            panel.part_list.setCurrentRow(row)
            return
    raise AssertionError(f"no row for {mpn_or_text}")


# --- with no chip source, the panel is what it was --------------------------


@pytest.mark.parametrize("chips", [False, NullChipSource()])
def test_with_no_chip_source_nothing_is_added_and_nothing_is_looked_up(qtbot, tmp_path, chips):
    held = _Held()
    panel = _panel(qtbot, chips=chips, library=_library(tmp_path), run=held)
    for row in range(panel.part_list.count()):
        panel.part_list.setCurrentRow(row)
        assert panel.chip_block.isHidden()
    assert held.jobs == []
    # Everything that was there before is still there, and still works.
    assert panel.part_list.count() == len(_PARTS)
    assert panel.use_button.isVisible() and panel.use_button.isEnabled()
    assert panel.search_edit.isVisible() and panel.qty_spin.isVisible()


def test_nothing_selected_shows_nothing(qtbot) -> None:
    panel = _panel(qtbot)
    assert panel.chip_block.isHidden()


# --- the four cases ---------------------------------------------------------


def test_a_part_with_no_mpn_shows_nothing(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, run=held)
    _select(panel, "Kapton tape")
    assert panel.chip_block.isHidden()
    assert held.jobs == []  # nothing to look up


def test_an_mpn_the_reference_does_not_know_says_so(qtbot, tmp_path) -> None:
    panel = _panel(qtbot, library=_library(tmp_path))
    _select(panel, "ZZ9999")
    assert panel.chip_block.isVisible()
    assert panel.chip_label.text() == "ZZ9999 isn't in the chip reference."
    assert panel.datasheet_row.isHidden()


def test_a_known_part_shows_what_the_reference_knows(qtbot, tmp_path) -> None:
    panel = _panel(qtbot, library=_library(tmp_path))
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text().splitlines() == [
        "EX1234QFN  ·  Example Semi",
        "QFN-20 (3x3)  ·  buck-controller, load-switch",
        "Single-phase buck controller for a standby rail",
        "Marked X1=, X9",
    ]
    assert panel.datasheet_row.isVisible()


def test_a_chip_with_little_known_shows_only_what_there_is(qtbot) -> None:
    panel = _panel(qtbot)
    _select(panel, "EX0001")
    assert panel.chip_label.text() == "EX0001  ·  Example Semi"


def test_the_part_number_is_matched_however_the_shelf_wrote_it(qtbot) -> None:
    parts = [Part(id=1, description="Buck", mpn="  ex1234qfn ", status="low")]
    panel = _panel(qtbot, parts=parts)
    panel.part_list.setCurrentRow(0)
    assert panel.chip_label.text().startswith("EX1234QFN  ·  Example Semi")


def test_a_datasheet_that_matches_by_hash_is_an_exact_copy(qtbot, tmp_path) -> None:
    opened: list[Path] = []
    library = _library(tmp_path, **{"downloaded (3).pdf": _REV_A})
    panel = _panel(qtbot, library=library, open_datasheet=lambda p: opened.append(p) or True)
    _select(panel, "EX1234QFN")
    assert panel.datasheet_label.text() == (
        "downloaded (3).pdf — an exact copy of the datasheet listed"
    )
    assert panel.datasheet_button.text() == "Open datasheet"
    assert panel.datasheet_button.isEnabled()
    panel.datasheet_button.click()
    assert opened == [library.root / "downloaded (3).pdf"]
    assert panel.message_label.text() == "Opened downloaded (3).pdf."


def test_a_datasheet_found_by_name_only_says_so_before_and_as_it_opens(qtbot, tmp_path) -> None:
    opened: list[Path] = []
    library = _library(tmp_path, **{"ex1234.PDF": _REV_C})  # the name, another revision
    panel = _panel(qtbot, library=library, open_datasheet=lambda p: opened.append(p) or True)
    _select(panel, "EX1234QFN")
    # Before it's opened: the line and the button both say it's a name match.
    assert panel.datasheet_label.text() == (
        "ex1234.PDF — matched by name only; it may be another revision"
    )
    assert panel.datasheet_button.text() == "Open datasheet (by name)"
    assert panel.datasheet_button.isEnabled()
    panel.datasheet_button.click()
    assert opened == [library.root / "ex1234.PDF"]
    # And as it opens.
    assert panel.message_label.text() == (
        "Opened ex1234.PDF — matched by name only, so check it's the right revision."
    )


def test_a_chip_with_no_datasheet_held_says_which_file_to_find(qtbot, tmp_path) -> None:
    opened: list[Path] = []
    library = _library(tmp_path, **{"something-else.pdf": b"other"})
    panel = _panel(qtbot, library=library, open_datasheet=lambda p: opened.append(p) or True)
    _select(panel, "EX1234QFN")
    assert panel.datasheet_label.text() == "Not held — the chip lists EX1234.pdf"
    assert not panel.datasheet_button.isEnabled()
    panel.open_selected_datasheet()  # even if asked, nothing opens
    assert opened == []


def test_a_chip_that_lists_no_datasheet_says_that(qtbot, tmp_path) -> None:
    panel = _panel(qtbot, library=_library(tmp_path, **{"EX9000.pdf": b"x"}))
    _select(panel, "EX9000SOT")
    assert panel.datasheet_label.text() == "No datasheet is listed for this chip"
    assert not panel.datasheet_button.isEnabled()


@pytest.mark.parametrize("library", [None, DatasheetLibrary()])
def test_with_no_datasheet_folder_the_details_still_show(qtbot, library) -> None:
    panel = _panel(qtbot, library=library)
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text().startswith("EX1234QFN")
    assert panel.datasheet_label.text() == "No datasheet folder set — the chip lists EX1234.pdf"
    assert not panel.datasheet_button.isEnabled()


def test_a_datasheet_that_will_not_open_is_said_not_swallowed(qtbot, tmp_path) -> None:
    panel = _panel(
        qtbot, library=_library(tmp_path, **{"EX1234.pdf": _REV_A}), open_datasheet=lambda p: False
    )
    _select(panel, "EX1234QFN")
    panel.datasheet_button.click()
    assert panel.message_label.text() == "Couldn't open EX1234.pdf."
    assert "d9534f" in panel.message_label.styleSheet()  # shown as an error


@pytest.mark.parametrize(
    ("file", "configured", "line", "button"),
    [
        (
            DatasheetFile(Path("/d/EX1234.pdf"), MatchedBy.HASH),
            True,
            "EX1234.pdf — an exact copy of the datasheet listed",
            "Open datasheet",
        ),
        (
            DatasheetFile(Path("/d/EX1234.pdf"), MatchedBy.NAME),
            True,
            "EX1234.pdf — matched by name only; it may be another revision",
            "Open datasheet (by name)",
        ),
        (None, True, "Not held — the chip lists EX1234.pdf", "Open datasheet"),
        (None, False, "No datasheet folder set — the chip lists EX1234.pdf", "Open datasheet"),
    ],
)
def test_what_the_datasheet_line_reads(file, configured: bool, line: str, button: str) -> None:
    assert datasheet_status(_BUCK, file, library_configured=configured) == (line, button)


def test_only_a_datasheet_counts_as_a_datasheet() -> None:
    chip = Chip(
        mpn="EX1",
        manufacturer="Example Semi",
        documents=(Document(kind="app-note", filename="EX1-layout.pdf"),),
    )
    assert datasheet_status(chip, None, library_configured=True)[0] == (
        "No datasheet is listed for this chip"
    )


# --- off the GUI thread, and never the wrong chip ---------------------------


def test_the_lookup_and_the_hashing_are_handed_to_the_worker(qtbot, tmp_path) -> None:
    asked: list[str] = []
    hashed: list[str] = []

    class Chips(InMemoryChipSource):
        def get(self, mpn: str):
            asked.append(mpn)
            return super().get(mpn)

    class Library(DatasheetLibrary):
        def datasheet(self, chip: Chip):
            hashed.append(chip.mpn)
            return super().datasheet(chip)

    folder = _library(tmp_path, **{"EX1234.pdf": _REV_A}).root
    held = _Held()
    panel = _panel(qtbot, chips=Chips([_BUCK], "community"), library=Library(folder), run=held)
    _select(panel, "EX1234QFN")
    # Selecting did nothing but ask for a worker.
    assert (asked, hashed) == ([], [])
    assert len(held.jobs) == 1
    assert panel.chip_label.text() == "Looking up EX1234QFN…"
    assert panel.datasheet_row.isHidden()
    held.run_next()
    assert (asked, hashed) == (["EX1234QFN"], ["EX1234QFN"])
    assert panel.datasheet_label.text().endswith("an exact copy of the datasheet listed")


def test_the_details_show_before_the_datasheet_has_been_found(qtbot, tmp_path) -> None:
    seen: list[tuple[str, str, bool]] = []

    class Library(DatasheetLibrary):
        def datasheet(self, chip: Chip):
            # The slow part. By now the chip is already on screen.
            seen.append(
                (
                    panel.chip_label.text().splitlines()[0],
                    panel.datasheet_label.text(),
                    panel.datasheet_button.isEnabled(),
                )
            )
            return super().datasheet(chip)

    folder = _library(tmp_path, **{"EX1234.pdf": _REV_A}).root
    panel = _panel(qtbot, library=Library(folder))
    _select(panel, "EX1234QFN")
    assert seen == [("EX1234QFN  ·  Example Semi", "Checking the datasheet folder…", False)]
    assert panel.datasheet_button.isEnabled()


def test_changing_the_selection_mid_lookup_never_shows_the_wrong_chip(qtbot, tmp_path) -> None:
    held = _Held()
    panel = _panel(qtbot, library=_library(tmp_path, **{"EX1234.pdf": _REV_A}), run=held)
    _select(panel, "EX1234QFN")
    _select(panel, "EX9000SOT")  # before the first answer is back
    assert len(held.jobs) == 1  # one worker at a time: the second waits its turn
    held.run_next()  # the answer for EX1234QFN arrives now
    assert panel.chip_label.text() == "Looking up EX9000SOT…"  # and is not shown
    assert panel.datasheet_row.isHidden()
    assert len(held.jobs) == 1  # the row that's selected is looked up next
    held.run_next()
    assert panel.chip_label.text().startswith("EX9000SOT  ·  Example Semi")
    assert panel.datasheet_label.text() == "No datasheet is listed for this chip"
    assert held.jobs == []


def test_arrowing_down_the_list_looks_up_only_the_row_you_stop_on(qtbot) -> None:
    asked: list[str] = []

    class Chips(InMemoryChipSource):
        def get(self, mpn: str):
            asked.append(mpn)
            return super().get(mpn)

    held = _Held()
    panel = _panel(qtbot, chips=Chips([_BUCK, _LDO, _BARE], "community"), run=held)
    for name in ("EX1234QFN", "EX9000SOT", "ZZ9999", "EX0001"):
        _select(panel, name)
    while held.jobs:
        held.run_next()
    assert asked == ["EX1234QFN", "EX0001"]  # the first, and where it stopped
    assert panel.chip_label.text() == "EX0001  ·  Example Semi"


def test_moving_to_a_part_with_no_mpn_mid_lookup_shows_nothing(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, run=held)
    _select(panel, "EX1234QFN")
    _select(panel, "Kapton tape")
    held.run_next()
    assert panel.chip_block.isHidden()
    assert held.jobs == []


def test_refreshing_the_list_mid_lookup_shows_nothing(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, run=held)
    _select(panel, "EX1234QFN")
    panel.refresh()  # the list is rebuilt: nothing is selected any more
    held.run_next()
    assert panel.chip_block.isHidden()


def test_an_answer_that_arrives_after_the_panel_is_gone_is_dropped(qtbot) -> None:
    held = _Held()
    inventory = InMemoryInventorySource(list(_PARTS))
    panel = InventoryPanel(
        lambda: inventory,
        chips=lambda: InMemoryChipSource([_BUCK], "community"),
        run_in_background=held,
    )
    panel.part_list.setCurrentRow(0)
    assert len(held.jobs) == 1
    panel.deleteLater()
    qtbot.wait(50)
    held.run_next()  # must not raise


def test_it_really_is_another_thread(qtbot, tmp_path) -> None:
    # The default runner, a real directory source and a real folder.
    build = tmp_path / "build"
    build.mkdir()
    shutil.copy(FIXTURE, build / "index.json")
    where: list[threading.Thread] = []

    class Chips(CompositeChipSource):
        def get(self, mpn: str):
            where.append(threading.current_thread())
            return super().get(mpn)

    class Library(DatasheetLibrary):
        def datasheet(self, chip: Chip):
            where.append(threading.current_thread())
            return super().datasheet(chip)

    folder = _library(tmp_path, **{"EX1234.pdf": _REV_C}).root
    inventory = InMemoryInventorySource(list(_PARTS))
    panel = InventoryPanel(
        lambda: inventory,
        chips=lambda: Chips([DirChipSource(root=build, name="community")]),
        datasheets=lambda: Library(folder),
    )
    qtbot.addWidget(panel)
    panel.part_list.setCurrentRow(0)
    qtbot.waitUntil(lambda: "by name" in panel.datasheet_button.text(), timeout=10_000)
    assert panel.chip_label.text().splitlines()[:2] == [
        "EX1234QFN  ·  Example Semi",
        "QFN-20 (3x3)  ·  buck-controller",
    ]
    # The fixture's fingerprint is made up, so no file can match it by hash.
    assert panel.datasheet_button.text() == "Open datasheet (by name)"
    assert len(where) == 2
    assert all(thread is not threading.main_thread() for thread in where)
    assert {thread.name for thread in where} == {"chips-lookup"}


# --- nothing raises into the HUD --------------------------------------------


def test_a_chip_source_that_raises_reads_as_not_known(qtbot, caplog) -> None:
    class Broken:
        name = "broken"

        def get(self, mpn: str):
            raise RuntimeError("get fell over")

        def by_marking(self, text: str):
            return []

    panel = _panel(qtbot, chips=Broken())
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text() == "EX1234QFN isn't in the chip reference."
    assert any("get fell over" in str(record.exc_info) for record in caplog.records)


def test_a_library_that_raises_reads_as_not_held(qtbot, tmp_path) -> None:
    class Broken(DatasheetLibrary):
        def datasheet(self, chip: Chip):
            raise RuntimeError("hashing fell over")

    panel = _panel(qtbot, library=Broken(tmp_path))
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text().startswith("EX1234QFN")
    assert panel.datasheet_label.text() == "Not held — the chip lists EX1234.pdf"
    assert not panel.datasheet_button.isEnabled()


def test_no_worker_to_be_had_is_said_and_the_next_selection_still_works(qtbot) -> None:
    workers = {"available": False}

    def run(job) -> None:
        if not workers["available"]:
            raise RuntimeError("can't start new thread")
        job()

    panel = _panel(qtbot, run=run)
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text() == "Couldn't look up EX1234QFN: can't start new thread"
    workers["available"] = True
    _select(panel, "EX9000SOT")
    assert panel.chip_label.text().startswith("EX9000SOT")


def test_an_answer_that_is_not_a_chip_reads_as_not_known(qtbot) -> None:
    class Odd:
        name = "odd"

        def get(self, mpn: str):
            return {"mpn": mpn}

        def by_marking(self, text: str):
            return []

    panel = _panel(qtbot, chips=Odd())
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text() == "EX1234QFN isn't in the chip reference."


# --- when the source or the folder changes ----------------------------------


def test_the_row_is_looked_up_again_when_asked(qtbot, tmp_path) -> None:
    current = {"chips": InMemoryChipSource([], "community"), "library": DatasheetLibrary()}
    inventory = InMemoryInventorySource(list(_PARTS))
    panel = InventoryPanel(
        lambda: inventory,
        chips=lambda: current["chips"],
        datasheets=lambda: current["library"],
        run_in_background=_now,
    )
    qtbot.addWidget(panel)
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text() == "EX1234QFN isn't in the chip reference."
    # Preferences OK: a source that knows it, and a folder that holds it.
    current["chips"] = InMemoryChipSource([_BUCK], "community")
    current["library"] = _library(tmp_path, **{"EX1234.pdf": _REV_A})
    panel.sync_chip_details()
    assert panel.chip_label.text().startswith("EX1234QFN  ·  Example Semi")
    assert panel.datasheet_button.isEnabled()
    # And switching chips off again takes the block away.
    current["chips"] = NullChipSource()
    panel.sync_chip_details()
    assert panel.chip_block.isHidden()


def test_using_a_part_still_works_with_chips_on(qtbot, tmp_path) -> None:
    panel = _panel(qtbot, library=_library(tmp_path, **{"EX1234.pdf": _REV_A}))
    _select(panel, "EX1234QFN")
    panel.qty_spin.setValue(1)
    panel.consume_selected()
    assert "Used 1" in panel.message_label.text()


# --- stream safety ----------------------------------------------------------


def test_the_panel_is_as_stream_safe_as_it_was() -> None:
    assert InventoryPanel.sensitive is False


def test_the_chip_details_are_about_a_part_number_and_nothing_else() -> None:
    summary = chip_summary(_BUCK)
    # Everything shown is one of the chip's own reference fields…
    shown = {"EX1234QFN", "Example Semi", "QFN-20 (3x3)", "buck-controller", "load-switch"}
    assert all(text in summary for text in shown)
    # …and the boards it's been seen on are not.
    for text in ("EXAMPLE-BOARD-1", "U7000", "standby 3.3 V"):
        assert text not in summary


def test_nothing_about_the_job_reaches_the_chip_details(qtbot, tmp_path) -> None:
    panel = _panel(
        qtbot,
        library=_library(tmp_path, **{"EX1234.pdf": _REV_A}),
        job_ref=lambda: "TICKET-1042 Jordan Example",
        open_datasheet=lambda p: True,
    )
    _select(panel, "EX1234QFN")
    panel.datasheet_button.click()
    for label in (panel.chip_label, panel.datasheet_label):
        assert "TICKET-1042" not in label.text()
        assert "Jordan" not in label.text()
    assert "TICKET-1042" not in panel.message_label.text()


# --- the app's datasheet library --------------------------------------------


@pytest.fixture
def worker_jobs(monkeypatch) -> list:
    """Jobs handed to the chips worker thread, held instead of run."""
    jobs: list = []
    monkeypatch.setattr(builder, "_thread", jobs.append)
    return jobs


def test_no_folder_is_a_library_that_finds_nothing_and_reads_nothing() -> None:
    for blank in ("", "   "):
        library = build_datasheet_library(blank)
        assert not library.configured
        ran: list = []
        assert warm_library(library, ran.append) is False
        assert ran == []
        assert library.datasheet(_BUCK) is None


def test_a_folder_is_listed_on_the_worker(tmp_path) -> None:
    folder = _library(tmp_path, **{"EX1234.pdf": _REV_A}).root
    library = build_datasheet_library(f"  {folder}  ")
    assert library.root == folder
    assert library._files is None  # built, not read
    ran: list = []
    assert warm_library(library, ran.append) is True
    assert library._files is None  # handed over, not done here
    ran[0]()
    assert library._files == (folder / "EX1234.pdf",)


def test_the_window_has_no_library_to_read_when_no_folder_is_set(qtbot, worker_jobs) -> None:
    window = MainWindow(scanner=_FakeScanner())
    qtbot.addWidget(window)
    assert not window.datasheet_library.configured
    assert worker_jobs == []  # no chip source, no folder: no thread at all


def test_the_window_builds_the_library_and_lists_it_off_the_gui_thread(
    qtbot, tmp_path, worker_jobs
) -> None:
    folder = _library(tmp_path, **{"EX1234.pdf": _REV_A}).root
    config = Config()
    config.chips.datasheet_dir = str(folder)
    window = MainWindow(config, scanner=_FakeScanner())
    qtbot.addWidget(window)
    library = window.datasheet_library
    assert library.root == folder
    assert len(worker_jobs) == 1
    assert library._files is None  # nothing listed on this thread
    worker_jobs.pop()()
    assert library._files == (folder / "EX1234.pdf",)


def test_ok_in_preferences_picks_up_a_changed_folder(qtbot, tmp_path, worker_jobs) -> None:
    first = _library(tmp_path / "a", **{"EX1234.pdf": _REV_A}).root
    second = _library(tmp_path / "b", **{"other.pdf": b"x"}).root
    config = Config()
    config.chips.datasheet_dir = str(first)
    window = MainWindow(config, scanner=_FakeScanner(), on_persist=lambda c: None)
    qtbot.addWidget(window)
    before = window.datasheet_library
    worker_jobs.clear()

    dialog = window.open_preferences()
    dialog.accept()  # nothing changed: the same library, listed again on the worker
    assert window.datasheet_library is before
    assert len(worker_jobs) == 1
    worker_jobs.clear()

    dialog = window.open_preferences()
    dialog.chips_datasheet_dir.setText(f" {second} ")
    dialog.accept()
    assert window.datasheet_library is not before
    assert window.datasheet_library.root == second
    assert window._config.chips.datasheet_dir == str(second)
    assert len(worker_jobs) == 1


def test_the_window_s_inventory_row_uses_its_chip_source_and_library(
    qtbot, tmp_path, worker_jobs
) -> None:
    build = tmp_path / "build"
    build.mkdir()
    shutil.copy(FIXTURE, build / "index.json")
    folder = _library(tmp_path, **{"EX1234.pdf": _REV_C}).root
    config = Config()
    config.chips.sources = [ChipSourceConfig("community", path=str(build))]
    config.chips.datasheet_dir = str(folder)
    window = MainWindow(config, scanner=_FakeScanner())
    qtbot.addWidget(window)
    while worker_jobs:
        worker_jobs.pop()()  # the start-up reads
    panel = window.inventory_panel
    window._inventory_source = InMemoryInventorySource(list(_PARTS))
    panel.refresh()
    panel._run_in_background = _now
    _select(panel, "EX1234QFN")
    assert panel.chip_label.text().splitlines()[0] == "EX1234QFN  ·  Example Semi"
    assert panel.datasheet_button.text() == "Open datasheet (by name)"
    assert panel.datasheet_label.text().startswith("EX1234.pdf — matched by name only")


def test_a_window_with_no_chip_sources_has_the_inventory_panel_it_always_had(qtbot) -> None:
    window = MainWindow(scanner=_FakeScanner())
    qtbot.addWidget(window)
    panel = window.inventory_panel
    window._inventory_source = InMemoryInventorySource(list(_PARTS))
    panel.refresh()
    for row in range(panel.part_list.count()):
        panel.part_list.setCurrentRow(row)
        assert panel.chip_block.isHidden()


# --- Preferences → Chips: the datasheet folder ------------------------------


def _prefs(qtbot, config: Config, **kwargs) -> PreferencesDialog:
    dialog = PreferencesDialog(config, _FakeScanner(), run_in_background=_now, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def test_the_chips_tab_shows_the_datasheet_folder(qtbot) -> None:
    config = Config()
    config.chips.datasheet_dir = "~/datasheets"
    dialog = _prefs(qtbot, config)
    assert dialog.chips_datasheet_dir.text() == "~/datasheets"
    assert dialog.chips_datasheet_dir.placeholderText() == "~/datasheets"
    assert _prefs(qtbot, Config()).chips_datasheet_dir.text() == ""


def test_browse_sets_the_datasheet_folder(qtbot, monkeypatch) -> None:
    dialog = _prefs(qtbot, Config())
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "/home/me/datasheets")
    dialog.browse_datasheet_dir_button.click()
    assert dialog.chips_datasheet_dir.text() == "/home/me/datasheets"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    dialog.browse_datasheet_dir_button.click()  # cancelled
    assert dialog.chips_datasheet_dir.text() == "/home/me/datasheets"


def test_ok_saves_the_datasheet_folder_and_cancel_does_not(qtbot) -> None:
    saved: list[Config] = []
    config = Config()
    dialog = _prefs(qtbot, config, on_save=saved.append)
    dialog.chips_datasheet_dir.setText("  ~/datasheets  ")
    dialog.reject()
    assert config.chips.datasheet_dir == ""
    dialog = _prefs(qtbot, config, on_save=saved.append)
    dialog.chips_datasheet_dir.setText("  ~/datasheets  ")
    dialog.accept()
    assert config.chips.datasheet_dir == "~/datasheets"
    assert saved == [config]
    dialog = _prefs(qtbot, config)
    dialog.chips_datasheet_dir.clear()
    dialog.apply_to_config()
    assert config.chips.datasheet_dir == ""
