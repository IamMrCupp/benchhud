"""Chips → Find chip… in the main window (#472), and what it's wired to.

The shared words for an opened datasheet, where-a-value-came-from in the
dialog, the dialog following a Preferences change, the menu item, and the
recon lock on its link-outs.

Offscreen, no network. Nothing here may open a real file or a real browser:
two autouse fixtures take both away and fail the test that reaches either.
Every chip is made up — "Example Semi" doesn't exist.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QMenu

import benchhud.chips as chips_package
from benchhud.chips import (
    Chip,
    CompositeChipSource,
    Document,
    InMemoryChipSource,
    Marking,
    NullChipSource,
    Substitute,
    UsedIn,
    builder,
)
from benchhud.chips import datasheets as datasheets_module
from benchhud.chips.datasheets import ChipDocument, DatasheetFile, DatasheetLibrary, MatchedBy
from benchhud.chips.find import Candidate, find_chips, tells_sources_apart, with_provenance
from benchhud.chips.linkout import MARKING_SITES
from benchhud.chips.opening import (
    Opened,
    held_line,
    not_held_line,
    open_held,
    opened_line,
)
from benchhud.config import ChipSourceConfig, Config
from benchhud.inventory import InMemoryInventorySource, Part
from benchhud.recon import Engagement, InMemoryReconSource
from benchhud.shell import chip_dialog
from benchhud.shell.chip_dialog import (
    MAX_SHOWN,
    NO_DATA,
    FindChipDialog,
    OpenOutcome,
    provenance_line,
)
from benchhud.shell.main_window import (
    LINK_OUT_LOCKED_BY_ENGAGEMENT,
    NO_DATASHEET_FOLDER,
    MainWindow,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

_REV_A = b"%PDF-1.4 EX1234 datasheet, revision A"
_REV_C = b"%PDF-1.4 EX1234 datasheet, revision C"
_SHEET = Document("datasheet", "EX1234.pdf", sha256=(hashlib.sha256(_REV_A).hexdigest(),))
_NOTE = Document("app-note", "EX1234-layout.pdf")

_BUCK = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    package="QFN-20 (3x3)",
    package_family="QFN",
    functions=("buck-controller",),
    description="Single-phase buck controller for a standby rail",
    markings=(Marking("X1=", ("X1=6A",)),),
    documents=(_SHEET, _NOTE),
    substitutes=(Substitute("EX1235QFN"),),
    used_in=(UsedIn("EXAMPLE-BOARD-1", "U7000"),),
)
# The operator's own notes on the same chip, and they disagree about two things.
_BUCK_LOCAL = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    package="QFN-24 (4x4)",
    description="Standby buck, runs hot",
    markings=(Marking("X1=", ("X1=9Z",)), Marking("XB")),
    used_in=(UsedIn("EXAMPLE-BOARD-9", "U1"),),
)
_LDO = Chip(mpn="EX9000SOT", manufacturer="Example Semi", package="SOT-23-5", functions=("ldo",))
# The same chip with the mark _BUCK has too: two candidates for "X1=".
_LDO_X1 = Chip(
    mpn="EX9000SOT",
    manufacturer="Example Semi",
    package="SOT-23-5",
    functions=("ldo",),
    markings=(Marking("X1=", ("X1=2B",)),),
)
_SWITCHLIKE = Chip(mpn="EX4410DFN", manufacturer="Example Semi", markings=(Marking("X4"),))


class _FakeScanner:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """A dialog the main window builds falls back to the real browser opener."""
    reached: list[str] = []
    monkeypatch.setattr(chip_dialog, "open_in_browser", lambda url: reached.append(url) or False)
    yield
    assert reached == [], "a test handed an address to the real browser opener"


@pytest.fixture(autouse=True)
def opened_files(monkeypatch) -> list[Path]:
    """Files handed to the OS to open — recorded here, never really opened."""
    opened: list[Path] = []
    monkeypatch.setattr(datasheets_module, "default_os_open", opened.append)
    return opened


def _inline(job) -> None:
    job()


def _library(tmp_path: Path, **files: bytes) -> DatasheetLibrary:
    folder = tmp_path / "datasheets"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_bytes(content)
    return DatasheetLibrary(folder)


def _two_sources() -> CompositeChipSource:
    return CompositeChipSource(
        [
            InMemoryChipSource([_BUCK_LOCAL], "local"),
            InMemoryChipSource([_BUCK, _LDO], "community"),
        ]
    )


def _dialog(qtbot, source, open_document=None, **kwargs) -> FindChipDialog:
    kwargs.setdefault("executor", _inline)
    dialog = FindChipDialog(source, open_document, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def _type(dialog: FindChipDialog, text: str) -> None:
    dialog.query_edit.setText(text)
    dialog.lookup_now()


def _part_numbers(dialog: FindChipDialog) -> list[str]:
    tree = dialog.tree
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def _details(dialog: FindChipDialog) -> dict[str, list[tuple[str, str, str, str]]]:
    """The detail list as ``{heading: [(col0, col1, col2, sources), ...]}``."""
    out: dict[str, list[tuple[str, str, str, str]]] = {}
    for i in range(dialog.details.topLevelItemCount()):
        head = dialog.details.topLevelItem(i)
        out[head.text(0)] = [
            tuple(head.child(j).text(column) for column in range(4))
            for j in range(head.childCount())
        ]
    return out


def _build_dir(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    build.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, build / "index.json")
    return build


# --- one set of words for a held document ----------------------------------


def _held(document: Document, how: MatchedBy, name: str = "whatever.pdf") -> ChipDocument:
    return ChipDocument(document, DatasheetFile(Path("/private/place") / name, how))


def test_the_words_for_an_exact_copy_and_a_name_match() -> None:
    exact, by_name = _held(_SHEET, MatchedBy.HASH), _held(_SHEET, MatchedBy.NAME)
    assert held_line(exact) == "EX1234.pdf — held, an exact copy"
    assert held_line(by_name) == (
        "EX1234.pdf — held, matched by name only; it may be another revision"
    )
    assert opened_line(exact, True) == "Opened EX1234.pdf."
    assert opened_line(by_name, True) == (
        "Opened EX1234.pdf — matched by name only, so check it's the right revision."
    )
    assert opened_line(exact, False) == opened_line(by_name, False) == "Couldn't open EX1234.pdf."


def test_the_words_for_nothing_held() -> None:
    assert not_held_line(_BUCK) == "Not held — the chip lists EX1234.pdf"
    assert not_held_line(_BUCK, library_configured=False) == (
        "No datasheet folder set — the chip lists EX1234.pdf"
    )
    assert not_held_line(_LDO) == "No datasheet is listed for this chip"
    # Asked for one document in particular: that one is named, datasheet or not.
    assert not_held_line(_BUCK, _NOTE) == "Not held — the chip lists EX1234-layout.pdf"


def test_opening_the_datasheet_says_which_kind_of_match_it_was(tmp_path, opened_files) -> None:
    exact = _library(tmp_path / "a", **{"saved by me.pdf": _REV_A})
    assert open_held(exact, _BUCK) == Opened(True, "Opened EX1234.pdf.")
    by_name = _library(tmp_path / "b", **{"ex1234.PDF": _REV_C})
    assert open_held(by_name, _BUCK) == Opened(
        True, "Opened EX1234.pdf — matched by name only, so check it's the right revision."
    )
    assert opened_files == [exact.root / "saved by me.pdf", by_name.root / "ex1234.PDF"]


def test_opening_one_document_in_particular(tmp_path, opened_files) -> None:
    library = _library(tmp_path, **{"EX1234-layout.pdf": b"layout", "EX1234.pdf": _REV_A})
    assert open_held(library, _BUCK, _NOTE) == Opened(
        True, "Opened EX1234-layout.pdf — matched by name only, so check it's the right revision."
    )
    assert opened_files == [library.root / "EX1234-layout.pdf"]


def test_nothing_held_opens_nothing_and_says_what_to_find(tmp_path, opened_files) -> None:
    empty = _library(tmp_path)
    assert open_held(empty, _BUCK) == Opened(False, "Not held — the chip lists EX1234.pdf.")
    assert open_held(empty, _BUCK, _NOTE) == Opened(
        False, "Not held — the chip lists EX1234-layout.pdf."
    )
    assert open_held(empty, _LDO) == Opened(False, "No datasheet is listed for this chip.")
    assert open_held(DatasheetLibrary(), _BUCK) == Opened(
        False, "No datasheet folder set — the chip lists EX1234.pdf."
    )
    assert opened_files == []


def test_a_file_that_will_not_open_is_said(tmp_path) -> None:
    library = _library(tmp_path, **{"EX1234.pdf": _REV_A})
    assert open_held(library, _BUCK, open_file=lambda path: False) == Opened(
        False, "Couldn't open EX1234.pdf."
    )


def test_what_is_said_never_names_the_file_on_this_machine(tmp_path) -> None:
    folder = tmp_path / "datasheets" / "clients" / "Jordan Example"
    folder.mkdir(parents=True)
    (folder / "smith-laptop (private).pdf").write_bytes(_REV_A)
    done = open_held(DatasheetLibrary(tmp_path / "datasheets"), _BUCK)
    assert done == Opened(True, "Opened EX1234.pdf.")
    for private in ("smith", "Jordan", "clients", str(tmp_path)):
        assert private not in done.detail


# --- which source said what -------------------------------------------------


def test_only_a_merge_of_several_sources_tells_them_apart() -> None:
    assert tells_sources_apart(_two_sources())
    assert not tells_sources_apart(CompositeChipSource([InMemoryChipSource([_BUCK], "only")]))
    assert not tells_sources_apart(CompositeChipSource([]))
    assert not tells_sources_apart(InMemoryChipSource([_BUCK], "plain"))
    assert not tells_sources_apart(NullChipSource())


def test_candidates_from_several_sources_carry_where_their_values_came_from() -> None:
    source = _two_sources()
    found = with_provenance(source, find_chips(source, "X1="))
    assert [c.chip.mpn for c in found] == ["EX1234QFN"]
    merged = found[0].merged
    assert merged is not None and merged.chip == found[0].chip
    assert merged.sources == ("local", "community")
    assert merged.source_of("package") == "local"
    assert [c.field for c in merged.conflicts] == ["package", "description"]


def test_candidates_from_one_source_carry_nothing_extra() -> None:
    for source in (
        InMemoryChipSource([_BUCK, _LDO], "plain"),
        CompositeChipSource([InMemoryChipSource([_BUCK, _LDO], "only")]),
    ):
        found = find_chips(source, "X1=")
        assert with_provenance(source, found) == found
        assert all(candidate.merged is None for candidate in found)


def test_provenance_is_fetched_only_for_the_candidates_that_will_be_shown() -> None:
    asked: list[str] = []

    class Counting(CompositeChipSource):
        def merged(self, mpn: str):
            asked.append(mpn)
            return super().merged(mpn)

    chips = [
        Chip(mpn=f"EX{n}", manufacturer="Example Semi", markings=(Marking("X"),)) for n in range(5)
    ]
    source = Counting([InMemoryChipSource(chips, "a"), InMemoryChipSource(chips, "b")])
    candidates = find_chips(source, "X")
    asked.clear()  # the lookup itself asks; what's counted is what comes after
    found = with_provenance(source, candidates, limit=2)
    assert len(found) == 5
    assert [c.merged is not None for c in found] == [True, True, False, False, False]
    assert asked == ["EX0", "EX1"]


def test_provenance_that_cannot_be_had_costs_only_itself() -> None:
    class Broken(CompositeChipSource):
        def merged(self, mpn: str):
            raise RuntimeError("merged fell over")

    class Stale(CompositeChipSource):
        def merged(self, mpn: str):
            real = super().merged(mpn)  # describes a chip that's since changed
            return None if real is None else type(real)(chip=_LDO, sources=real.sources)

    class NotOne(CompositeChipSource):
        def merged(self, mpn: str):
            return {"mpn": mpn}

    sources = [InMemoryChipSource([_BUCK], "a"), InMemoryChipSource([_BUCK], "b")]
    candidates = find_chips(CompositeChipSource(sources), "X1=")
    for kind in (Broken, Stale, NotOne):
        found = with_provenance(kind(sources), candidates)
        assert [c.chip.mpn for c in found] == ["EX1234QFN"]
        assert found[0].merged is None


def test_the_line_that_says_which_source_said_what() -> None:
    merged = _two_sources().merged("EX1234QFN")
    # The package family isn't borrowed from a source whose package lost.
    assert provenance_line(merged) == (
        "From local: maker, package, description."
        "  Sources disagree on package, description — every value is listed below."
    )
    agreed = CompositeChipSource(
        [InMemoryChipSource([_LDO], "local"), InMemoryChipSource([_LDO], "community")]
    ).merged("EX9000SOT")
    assert provenance_line(agreed) == "From local: maker, package"
    filled_in = CompositeChipSource(
        [
            InMemoryChipSource([Chip(mpn="EX1234QFN", manufacturer="Example Semi")], "local"),
            InMemoryChipSource([_BUCK], "community"),
        ]
    ).merged("EX1234QFN")
    assert provenance_line(filled_in) == (
        "From local: maker  ·  from community: package, description, package family"
    )


def test_with_several_sources_the_detail_pane_says_where_each_part_came_from(qtbot) -> None:
    dialog = _dialog(qtbot, _two_sources())
    _type(dialog, "X1=")
    assert dialog.selected().chip.mpn == "EX1234QFN"
    assert dialog.sources_label.isVisibleTo(dialog)
    assert dialog.sources_label.text().startswith("From local: maker, package, description")
    assert dialog.sources_label.textFormat() == Qt.TextFormat.PlainText

    details = _details(dialog)
    # Every source's value, the one in use first and marked.
    assert details["Sources disagree"] == [
        ("package", "QFN-24 (4x4)", "shown", "local"),
        ("package", "QFN-20 (3x3)", "", "community"),
        ("description", "Standby buck, runs hot", "shown", "local"),
        ("description", "Single-phase buck controller for a standby rail", "", "community"),
    ]
    assert next(iter(details)) == "Sources disagree"  # first, where it can't be missed
    # Each row of a list names the sources that have it.
    assert [(row[0], row[3]) for row in details["Markings"]] == [
        ("X1=", "local, community"),
        ("XB", "local"),
    ]
    assert [(row[1], row[3]) for row in details["Documents"]] == [
        ("EX1234.pdf", "community"),
        ("EX1234-layout.pdf", "community"),
    ]
    assert [(row[0], row[3]) for row in details["Substitutes"]] == [("EX1235QFN", "community")]
    assert [(row[0], row[3]) for row in details["Seen on"]] == [
        ("EXAMPLE-BOARD-9", "local"),
        ("EXAMPLE-BOARD-1", "community"),
    ]


def test_sources_that_agree_show_no_dispute(qtbot) -> None:
    source = CompositeChipSource(
        [InMemoryChipSource([_LDO], "local"), InMemoryChipSource([_LDO], "community")]
    )
    dialog = _dialog(qtbot, source)
    _type(dialog, "EX9000SOT")
    assert dialog.sources_label.text() == "From local: maker, package"
    assert "Sources disagree" not in _details(dialog)


@pytest.mark.parametrize(
    "source",
    [
        InMemoryChipSource([_BUCK, _LDO], "plain"),
        CompositeChipSource([InMemoryChipSource([_BUCK, _LDO], "only")]),
    ],
)
def test_with_one_source_the_detail_pane_is_as_it_was(qtbot, source) -> None:
    dialog = _dialog(qtbot, source)
    _type(dialog, "EX1234QFN")
    assert not dialog.sources_label.isVisibleTo(dialog)
    assert dialog.sources_label.text() == ""
    details = _details(dialog)
    assert list(details) == ["Markings", "Documents", "Substitutes", "Seen on"]
    assert all(row[3] == "" for rows in details.values() for row in rows)


def test_with_one_source_the_detail_list_is_laid_out_as_it_was(qtbot) -> None:
    # Before there was a fourth column the third was the last, and took the
    # spare width. A long note mustn't now need a scrollbar it never needed.
    wordy = Chip(
        mpn="EX1234QFN",
        manufacturer="Example Semi",
        markings=(Marking("X1=", ("X1=6A",), "a note " * 20),),
    )
    dialog = _dialog(qtbot, InMemoryChipSource([wordy], "plain"))
    dialog.show()
    _type(dialog, "EX1234QFN")
    header = dialog.details.header()
    assert dialog.details.isColumnHidden(3)
    assert header.stretchLastSection()
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.ResizeToContents
    assert not dialog.details.horizontalScrollBar().isVisible()


def test_with_several_sources_the_fourth_column_appears_beside_a_stretching_third(qtbot) -> None:
    dialog = _dialog(qtbot, _two_sources())
    _type(dialog, "X1=")
    header = dialog.details.header()
    assert not dialog.details.isColumnHidden(3)
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Stretch
    # And it goes away again when a chip from one source — or none — is shown.
    dialog.set_source(InMemoryChipSource([_BUCK], "plain"))
    assert dialog.details.isColumnHidden(3)
    _type(dialog, "")
    assert dialog.details.isColumnHidden(3)


def test_where_values_came_from_is_fetched_with_the_lookup_not_on_a_pick(qtbot) -> None:
    asked: list[str] = []

    class Counting(CompositeChipSource):
        def merged(self, mpn: str):
            asked.append(mpn)
            return super().merged(mpn)

    jobs: list = []
    source = Counting(
        [InMemoryChipSource([_BUCK_LOCAL], "local"), InMemoryChipSource([_BUCK, _LDO], "community")]
    )
    dialog = _dialog(qtbot, source, executor=jobs.append)
    dialog.query_edit.setText("X1=")
    dialog.lookup_now()
    assert asked == []  # nothing on this thread
    jobs.pop(0)()
    fetched = len(asked)
    assert fetched > 0
    for row in range(dialog.tree.topLevelItemCount()):
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(row))
    assert len(asked) == fetched  # picking a row asks the source nothing
    assert dialog.sources_label.text() != ""


def test_only_the_candidates_that_will_be_listed_have_it_fetched(qtbot) -> None:
    asked: list[str] = []

    class Counting(CompositeChipSource):
        def merged(self, mpn: str):
            asked.append(mpn)
            return super().merged(mpn)

    chips = [
        Chip(mpn=f"EX{n:04}", manufacturer="Example Semi", markings=(Marking("X"),))
        for n in range(MAX_SHOWN + 50)
    ]
    source = Counting([InMemoryChipSource(chips, "a"), InMemoryChipSource(chips, "b")])
    dialog = _dialog(qtbot, source)
    _type(dialog, "X")
    # One ask is the lookup's own (is "X" a part number?); the rest are provenance.
    assert len(asked) == MAX_SHOWN + 1
    assert dialog.tree.topLevelItemCount() == MAX_SHOWN


def test_source_names_and_disputed_values_stay_out_of_tooltips(qtbot) -> None:
    dialog = _dialog(qtbot, _two_sources())
    _type(dialog, "X1=")
    from_data = ("QFN-24", "Standby buck", "local", "community", "EX1234", "XB")
    tips = [dialog.sources_label.toolTip()]
    for i in range(dialog.details.topLevelItemCount()):
        head = dialog.details.topLevelItem(i)
        for j in range(head.childCount()):
            tips += [head.child(j).toolTip(column) for column in range(4)]
    for tip in tips:
        assert not any(text in tip for text in from_data)


# --- following Preferences ----------------------------------------------------


def test_a_new_source_is_looked_in_at_once_with_the_text_kept(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([_LDO], "old"))
    _type(dialog, "EX1234QFN")
    assert _part_numbers(dialog) == []
    dialog.set_source(InMemoryChipSource([_BUCK], "new"))
    assert dialog.query_edit.text() == "EX1234QFN"
    assert _part_numbers(dialog) == ["EX1234QFN"]
    dialog.set_source(InMemoryChipSource([_LDO], "back"))
    assert _part_numbers(dialog) == []


def test_an_answer_from_the_old_source_is_not_shown(qtbot) -> None:
    jobs: list = []
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK], "old"), executor=jobs.append)
    _type(dialog, "X1=")
    assert len(jobs) == 1  # the old source is working on it
    dialog.set_source(InMemoryChipSource([_LDO], "new"))
    jobs.pop(0)()  # its answer arrives now
    assert _part_numbers(dialog) == []  # and isn't shown
    assert len(jobs) == 1  # the new source is asked instead
    jobs.pop(0)()
    assert _part_numbers(dialog) == []  # the new one has no chip with that mark
    dialog.query_edit.setText("EX9000SOT")
    dialog.lookup_now()
    jobs.pop(0)()
    assert _part_numbers(dialog) == ["EX9000SOT"]


class _Failing(InMemoryChipSource):
    def by_marking(self, text: str):
        raise RuntimeError("the old source fell over")


def test_a_failure_from_the_old_source_is_not_pinned_on_the_new(qtbot) -> None:
    jobs: list = []
    dialog = _dialog(qtbot, _Failing([_BUCK], "old"), executor=jobs.append)
    dialog.query_edit.setText("X1=")
    dialog.lookup_now()
    dialog.set_source(InMemoryChipSource([_BUCK], "new"))
    jobs.pop(0)()  # the old source's lookup fails now
    assert "failed" not in dialog.status_label.text()
    assert len(jobs) == 1  # and the new source is asked
    jobs.pop(0)()
    assert _part_numbers(dialog) == ["EX1234QFN"]
    assert dialog.status_label.text() == "1 candidate."


def test_a_failure_from_a_source_that_was_taken_away_says_nothing(qtbot) -> None:
    jobs: list = []
    dialog = _dialog(qtbot, _Failing([_BUCK], "old"), executor=jobs.append)
    dialog.query_edit.setText("X1=")
    dialog.lookup_now()
    dialog.set_source(NullChipSource())
    dialog._on_slow()  # the "still looking" timer firing late
    assert dialog.status_label.text() == ""
    jobs.pop(0)()
    assert dialog.status_label.text() == ""  # the notice is the whole story
    assert dialog.notice_label.isVisibleTo(dialog)
    assert jobs == []


def test_a_failure_from_the_source_in_use_is_still_a_failure(qtbot) -> None:
    dialog = _dialog(qtbot, _Failing([_BUCK], "only"))
    _type(dialog, "X1=")
    assert dialog.status_label.text().startswith("The lookup failed")


def test_the_old_source_s_rows_go_the_moment_it_is_swapped(qtbot) -> None:
    jobs: list = []
    opened: list[str] = []
    dialog = _dialog(
        qtbot,
        InMemoryChipSource([_BUCK, _LDO_X1], "old"),
        lambda chip, document: opened.append(chip.mpn) or True,
        executor=jobs.append,
    )
    dialog.query_edit.setText("X1=")
    dialog.lookup_now()
    jobs.pop(0)()
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))  # the operator picks EX9000SOT
    assert dialog.selected().chip.mpn == "EX9000SOT"

    dialog.set_source(InMemoryChipSource([_LDO_X1, _BUCK], "new"))
    # Before the new source has answered: nothing of the old one's is left to act on.
    assert _part_numbers(dialog) == []
    assert dialog.selected() is None
    assert not dialog.open_selected()
    assert opened == []
    assert dialog.status_label.text() == "Looking up “X1=”…"
    assert not dialog.sources_label.isVisibleTo(dialog)

    jobs.pop(0)()
    assert sorted(_part_numbers(dialog)) == ["EX1234QFN", "EX9000SOT"]
    assert dialog.selected().chip.mpn == "EX9000SOT"  # the pick is made again


def test_a_pick_the_new_source_does_not_have_is_not_made_again_later(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK, _LDO_X1], "old"))
    _type(dialog, "X1=")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))
    assert dialog.selected().chip.mpn == "EX9000SOT"
    dialog.set_source(InMemoryChipSource([_BUCK], "new"))  # no EX9000SOT here
    assert _part_numbers(dialog) == ["EX1234QFN"]
    # Back on a source that has it: the old pick isn't sprung on a later list.
    dialog.set_source(InMemoryChipSource([_BUCK, _LDO_X1, _SWITCHLIKE], "newer"))
    _type(dialog, "X")
    assert len(_part_numbers(dialog)) == 3
    assert dialog.selected() is None


def test_an_arrow_pressed_before_the_swap_lands_on_the_new_list(qtbot) -> None:
    jobs: list = []
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK, _LDO_X1], "old"), executor=jobs.append)
    dialog.query_edit.setText("X1=")
    dialog._move(1)  # ↓ while the list is still behind the box
    dialog.set_source(InMemoryChipSource([_BUCK, _LDO_X1], "new"))
    while jobs:
        jobs.pop(0)()
    assert dialog.selected() is not None
    assert dialog.selected().chip.mpn == _part_numbers(dialog)[0]


def test_the_same_source_can_be_told_to_read_its_folders_again(qtbot) -> None:
    reloads: list[str] = []

    class Reloading(InMemoryChipSource):
        def reload(self) -> None:
            reloads.append("reload")
            self.chips.append(_LDO_X1)  # what a pull would have brought

    jobs: list = []
    source = Reloading([_BUCK], "same")
    dialog = _dialog(qtbot, source, executor=jobs.append)
    dialog.query_edit.setText("X1=")
    dialog.lookup_now()
    jobs.pop(0)()
    assert _part_numbers(dialog) == ["EX1234QFN"]

    dialog.set_source(source, reread=True)
    assert reloads == []  # not on this thread
    jobs.pop(0)()
    assert reloads == ["reload"]
    assert sorted(_part_numbers(dialog)) == ["EX1234QFN", "EX9000SOT"]
    # Once, not before every lookup from now on.
    dialog.query_edit.setText("X1")
    dialog.lookup_now()
    jobs.pop(0)()
    assert reloads == ["reload"]


def test_a_source_taken_away_says_there_is_no_chip_data(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK], "was"))
    _type(dialog, "X1=")
    assert _part_numbers(dialog) == ["EX1234QFN"]
    dialog.set_source(NullChipSource())
    assert _part_numbers(dialog) == []
    assert dialog.notice_label.isVisibleTo(dialog)
    assert dialog.notice_label.text() == NO_DATA
    assert dialog.status_label.text() == ""
    assert dialog.query_edit.text() == "X1="  # what was typed is still there
    dialog.set_source(InMemoryChipSource([_BUCK], "back"))
    assert not dialog.notice_label.isVisibleTo(dialog)
    assert _part_numbers(dialog) == ["EX1234QFN"]


def test_the_caller_can_still_say_a_new_source_is_not_configured(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK], "was"))
    dialog.set_source(CompositeChipSource([]), configured=False)
    assert dialog.notice_label.isVisibleTo(dialog)
    _type(dialog, "X1=")
    assert _part_numbers(dialog) == []


def test_no_chip_data_points_at_where_to_add_some() -> None:
    assert "Preferences → Chips" in NO_DATA


def test_an_opener_given_later_turns_open_datasheet_on(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK], "s"), open_unavailable="No folder yet.")
    _type(dialog, "EX1234QFN")
    assert not dialog.open_button.isEnabled()
    assert dialog.open_label.text() == "No folder yet."
    opened: list[str] = []
    dialog.set_open_document(lambda chip, document: opened.append(chip.mpn) or True)
    assert dialog.open_button.isEnabled()
    assert dialog.open_label.text() == ""
    assert dialog.open_label.objectName() == ""  # no longer drawn as a muted reason
    assert dialog.open_selected()
    assert opened == ["EX1234QFN"]


def test_an_opener_taken_away_turns_it_off_and_says_why(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK], "s"), lambda chip, document: True)
    _type(dialog, "EX1234QFN")
    assert dialog.open_button.isEnabled()
    dialog.set_open_document(None, "The folder was cleared.")
    assert not dialog.open_button.isEnabled()
    assert dialog.open_label.text() == "The folder was cleared."
    assert dialog.open_label.objectName() == "hint"
    assert dialog.open_button.toolTip() == "The folder was cleared."
    assert not dialog.open_selected()


def test_opening_turned_off_mid_open_says_why_and_not_looking_for_ever(qtbot) -> None:
    jobs: list = []
    dialog = _dialog(
        qtbot,
        InMemoryChipSource([_BUCK], "s"),
        lambda chip, document: OpenOutcome(True, "Opened by the one that's gone."),
        executor=jobs.append,
    )
    dialog.query_edit.setText("EX1234QFN")
    dialog.lookup_now()
    jobs.pop(0)()
    assert dialog.open_selected()
    assert dialog.open_label.text().startswith("Looking for")
    dialog.set_open_document(None, "The folder was cleared.")
    jobs.pop(0)()  # the opener that was running finishes
    assert dialog.open_label.text() == "The folder was cleared."
    assert not dialog.open_button.isEnabled()


def test_changing_the_opener_leaves_a_message_and_a_picked_row_alone(qtbot) -> None:
    dialog = _dialog(
        qtbot,
        InMemoryChipSource([_BUCK], "s"),
        lambda chip, document: OpenOutcome(True, "Opened EX1234.pdf."),
    )
    _type(dialog, "EX1234QFN")
    assert dialog.open_selected()
    assert dialog.open_label.text() == "Opened EX1234.pdf."
    documents = next(
        dialog.details.topLevelItem(i)
        for i in range(dialog.details.topLevelItemCount())
        if dialog.details.topLevelItem(i).text(0) == "Documents"
    )
    dialog.details.setCurrentItem(documents.child(1))
    dialog.set_open_document(lambda chip, document: True)  # another folder, still able to open
    assert dialog.open_label.text() == "Opened EX1234.pdf."
    assert dialog.details.currentItem() is documents.child(1)


def test_the_document_rows_say_what_enter_does_now(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([_BUCK], "s"), open_unavailable="No folder yet.")
    _type(dialog, "EX1234QFN")

    def tips() -> set[str]:
        out: set[str] = set()
        for i in range(dialog.details.topLevelItemCount()):
            head = dialog.details.topLevelItem(i)
            if head.text(0) == "Documents":
                out |= {head.child(j).toolTip(0) for j in range(head.childCount())}
        return out

    assert tips() == {"No folder yet."}
    dialog.set_open_document(lambda chip, document: True)
    assert tips() == {"Enter opens this document."}
    dialog.set_open_document(None, "The folder was cleared.")
    assert tips() == {"The folder was cleared."}


def test_an_opener_swapped_mid_open_lets_the_running_one_finish(qtbot) -> None:
    jobs: list = []
    dialog = _dialog(
        qtbot,
        InMemoryChipSource([_BUCK], "s"),
        lambda chip, document: OpenOutcome(True, "Opened by the first."),
        executor=jobs.append,
    )
    dialog.query_edit.setText("EX1234QFN")
    dialog.lookup_now()
    jobs.pop(0)()
    assert dialog.open_selected()
    dialog.set_open_document(lambda chip, document: OpenOutcome(True, "Opened by the second."))
    jobs.pop(0)()
    assert dialog.open_label.text() == "Opened by the first."


# --- the menu item ------------------------------------------------------------


def _window(qtbot, config: Config | None = None, **kwargs) -> MainWindow:
    window = MainWindow(config, scanner=_FakeScanner(), **kwargs)
    qtbot.addWidget(window)
    return window


@pytest.fixture
def worker_jobs(monkeypatch) -> list:
    """Jobs handed to the chips worker thread, held instead of run."""
    jobs: list = []
    monkeypatch.setattr(builder, "_thread", jobs.append)
    return jobs


def _configured(tmp_path: Path, *, datasheets: DatasheetLibrary | None = None) -> Config:
    config = Config()
    config.chips.sources = [ChipSourceConfig("community", path=str(_build_dir(tmp_path)))]
    if datasheets is not None:
        config.chips.datasheet_dir = str(datasheets.root)
    return config


def _settle(worker_jobs: list) -> None:
    while worker_jobs:
        worker_jobs.pop()()


def test_there_is_a_chips_menu_with_find_chip_in_it(qtbot) -> None:
    window = _window(qtbot)
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles.index("Chips") == titles.index("Boards") + 1  # beside the one it's most like
    menus = {menu.title(): menu for menu in window.menuBar().findChildren(QMenu)}
    assert [action.text() for action in menus["Chips"].actions()] == ["Find chip…"]
    assert menus["Chips"].actions()[0] is window.find_chip_action


def test_find_chip_takes_no_key_that_something_else_might_want(qtbot) -> None:
    window = _window(qtbot)
    assert window.find_chip_action.shortcut().isEmpty()
    assert window.find_board_action.shortcut().isEmpty()  # the convention it follows


def test_the_menu_item_opens_the_dialog_beside_the_window_not_over_it(qtbot) -> None:
    window = _window(qtbot)
    window.find_chip_action.trigger()
    dialog = window._chip_dialog
    assert isinstance(dialog, FindChipDialog)
    assert dialog.isVisible()
    assert dialog.parent() is window
    assert not dialog.isModal()  # the HUD stays usable while it's open


def test_it_is_one_dialog_shown_again_with_what_was_typed(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, _configured(tmp_path))
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "X1=")
    dialog.close()
    again = window.open_chip_search()
    assert again is dialog
    assert again.isVisible()
    assert again.query_edit.text() == "X1="
    assert _part_numbers(again) == ["EX1234QFN", "EX9000SOT"]


def test_with_no_chip_sources_the_dialog_says_where_to_add_one(qtbot) -> None:
    dialog = _window(qtbot).open_chip_search()
    assert dialog.notice_label.isVisibleTo(dialog)
    assert "Preferences → Chips" in dialog.notice_label.text()


def test_the_dialog_looks_in_the_window_s_chip_source(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, _configured(tmp_path))
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    assert not dialog.notice_label.isVisibleTo(dialog)
    _type(dialog, "X1=6A")
    assert _part_numbers(dialog) == ["EX1234QFN", "EX9000SOT"]
    _type(dialog, "SD77/NOPB")
    assert _part_numbers(dialog) == ["SD77/NOPB"]


# --- the opener over the datasheet library --------------------------------------


def test_with_no_datasheet_folder_open_is_off_and_points_at_preferences(qtbot, tmp_path) -> None:
    dialog = _window(qtbot).open_chip_search()
    assert dialog.open_label.text() == NO_DATASHEET_FOLDER
    assert "Preferences → Chips" in NO_DATASHEET_FOLDER
    assert not dialog.open_button.isEnabled()


def test_find_chip_opens_the_held_datasheet_and_says_how_it_matched(
    qtbot, tmp_path, worker_jobs, opened_files
) -> None:
    # The fixture's fingerprint is made up, so a file can only match it by name.
    library = _library(tmp_path, **{"EX1234.pdf": _REV_C})
    window = _window(qtbot, _configured(tmp_path, datasheets=library))
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "EX1234QFN")
    assert dialog.open_button.isEnabled()
    assert dialog.open_selected()
    assert opened_files == [library.root / "EX1234.pdf"]
    chip = dialog.selected().chip
    # The same words the inventory row uses, from the same place.
    held = ChipDocument(
        chip.documents[0], DatasheetFile(library.root / "EX1234.pdf", MatchedBy.NAME)
    )
    assert dialog.open_label.text() == opened_line(held, True)
    assert "matched by name only" in dialog.open_label.text()


def test_the_window_s_opener_says_an_exact_copy_is_one(qtbot, tmp_path, opened_files) -> None:
    library = _library(tmp_path, **{"renamed.pdf": _REV_A})
    config = Config()
    config.chips.datasheet_dir = str(library.root)
    window = _window(qtbot, config)
    assert window._chip_opener() is not None
    assert window._open_chip_document(_BUCK, None) == OpenOutcome(True, "Opened EX1234.pdf.")
    assert window._open_chip_document(_BUCK, _NOTE) == OpenOutcome(
        False, "Not held — the chip lists EX1234-layout.pdf."
    )
    assert opened_files == [library.root / "renamed.pdf"]


def test_the_window_s_opener_searches_whichever_folder_is_current(
    qtbot, tmp_path, opened_files
) -> None:
    first = _library(tmp_path / "a", **{"EX1234.pdf": _REV_A})
    second = _library(tmp_path / "b", **{"somewhere-else.pdf": _REV_A})
    config = Config()
    config.chips.datasheet_dir = str(first.root)
    window = _window(qtbot, config, on_persist=lambda c: None)
    opener = window._chip_opener()
    assert opener(_BUCK, None).opened
    # Preferences moves the folder; the opener the dialog already holds follows.
    prefs = window.open_preferences()
    prefs.chips_datasheet_dir.setText(str(second.root))
    prefs.accept()
    assert opener(_BUCK, None).opened
    assert opened_files == [first.root / "EX1234.pdf", second.root / "somewhere-else.pdf"]


def test_the_inventory_row_and_find_chip_say_the_same_thing(
    qtbot, tmp_path, worker_jobs, opened_files
) -> None:
    library = _library(tmp_path, **{"EX1234.pdf": _REV_C})
    window = _window(qtbot, _configured(tmp_path, datasheets=library))
    _settle(worker_jobs)

    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "EX1234QFN")
    dialog.open_selected()

    panel = window.inventory_panel
    window._inventory_source = InMemoryInventorySource(
        [Part(id=1, description="Buck", mpn="EX1234QFN", status="low")]
    )
    panel.refresh()
    panel._run_in_background = _inline
    panel.part_list.setCurrentRow(0)
    panel.datasheet_button.click()

    assert dialog.open_label.text() == panel.message_label.text()
    assert len(opened_files) == 2


# --- following Preferences, from the window -------------------------------------


def test_ok_in_preferences_gives_the_open_dialog_the_new_sources(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, on_persist=lambda config: None)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "X1=")
    assert dialog.notice_label.isVisibleTo(dialog)

    prefs = window.open_preferences()
    prefs.add_chip_source_button.click()
    prefs.chips_table.item(0, 0).setText("community")
    prefs.chips_table.item(0, 1).setText(str(_build_dir(tmp_path)))
    prefs.accept()
    _settle(worker_jobs)

    assert window.open_chip_search() is dialog  # the same window, not a new one
    assert not dialog.notice_label.isVisibleTo(dialog)
    assert dialog.query_edit.text() == "X1="
    assert _part_numbers(dialog) == ["EX1234QFN", "EX9000SOT"]


def test_ok_in_preferences_turns_open_datasheet_on_and_off_in_the_open_dialog(
    qtbot, tmp_path, worker_jobs
) -> None:
    library = _library(tmp_path, **{"EX1234.pdf": _REV_C})
    window = _window(qtbot, _configured(tmp_path), on_persist=lambda config: None)
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "EX1234QFN")
    assert not dialog.open_button.isEnabled()
    assert dialog.open_label.text() == NO_DATASHEET_FOLDER

    prefs = window.open_preferences()
    prefs.chips_datasheet_dir.setText(str(library.root))
    prefs.accept()
    assert dialog.open_button.isEnabled()
    assert dialog.open_label.text() == ""

    prefs = window.open_preferences()
    prefs.chips_datasheet_dir.clear()
    prefs.accept()
    assert not dialog.open_button.isEnabled()
    assert dialog.open_label.text() == NO_DATASHEET_FOLDER


def test_ok_with_the_sources_unchanged_still_shows_what_changed_on_disk(
    qtbot, tmp_path, worker_jobs
) -> None:
    folder = tmp_path / "chips-private" / "chips" / "example-semi"
    folder.mkdir(parents=True)

    def write(mpn: str, package: str) -> None:
        (folder / f"{mpn}.toml").write_text(
            f'schema = 1\nmpn = "{mpn}"\nmanufacturer = "Example Semi"\n'
            f'package = "{package}"\n[[markings]]\ncode = "X1="\n'
        )

    write("EX1234QFN", "QFN-20 (3x3)")
    config = Config()
    config.chips.sources = [ChipSourceConfig("local", path=str(tmp_path / "chips-private"))]
    window = _window(qtbot, config, on_persist=lambda c: None)
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "X1=")
    assert [dialog.tree.topLevelItem(0).text(2)] == ["QFN-20 (3x3)"]

    write("EX1234QFN", "QFN-24 (4x4)")  # edited on disk
    write("EX5555QFN", "QFN-16")  # and one added
    window.open_preferences().accept()  # nothing changed in Preferences
    _settle(worker_jobs)

    assert window.open_chip_search() is dialog
    assert dialog.query_edit.text() == "X1="
    assert sorted(_part_numbers(dialog)) == ["EX1234QFN", "EX5555QFN"]
    packages = {
        dialog.tree.topLevelItem(i).text(0): dialog.tree.topLevelItem(i).text(2)
        for i in range(dialog.tree.topLevelItemCount())
    }
    assert packages["EX1234QFN"] == "QFN-24 (4x4)"


def test_a_dialog_never_opened_is_not_made_by_a_preferences_ok(qtbot) -> None:
    window = _window(qtbot, on_persist=lambda config: None)
    window.open_preferences().accept()
    assert window._chip_dialog is None


# --- the recon lock ---------------------------------------------------------------

_ENGAGEMENT = Engagement("R26-0004", title="Made-up teardown", target_class="iot-lock")


def test_with_no_engagement_the_link_outs_are_on(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, _configured(tmp_path))
    _settle(worker_jobs)
    assert window.may_link_out() is True
    dialog = window.open_chip_search()
    dialog.query_edit.setText("X1=")
    assert all(button.isEnabled() for button in dialog.site_buttons)
    assert "Web lookups are off" not in dialog.web_label.text()


def test_an_imported_engagement_turns_the_link_outs_off_and_says_how_long(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, _configured(tmp_path))
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog.query_edit.setText("X1=")
    window._recon_source = InMemoryReconSource(engagements=[_ENGAGEMENT])
    assert window.import_engagement("R26-0004") is not None

    assert window.may_link_out() is False
    # Straight away, with the dialog still open and nothing typed or clicked.
    assert not any(button.isEnabled() for button in dialog.site_buttons)
    assert not dialog.maker_button.isEnabled()
    assert dialog.web_label.text() == LINK_OUT_LOCKED_BY_ENGAGEMENT
    assert "End the engagement from the Engagement panel" in dialog.web_label.text()
    # The engagement itself is not named on the dialog.
    assert "R26-0004" not in dialog.web_label.text()
    assert "teardown" not in dialog.web_label.text()
    # And a click that got through anyway hands nothing over.
    assert dialog.search_on(MARKING_SITES[0]) is False


def test_a_ref_that_does_not_resolve_locks_nothing(qtbot) -> None:
    window = _window(qtbot)
    window._recon_source = InMemoryReconSource(engagements=[_ENGAGEMENT])
    assert window.import_engagement("R26-9999") is None
    assert window.may_link_out() is True


def test_nothing_but_ending_it_ends_an_engagement(qtbot, tmp_path, worker_jobs) -> None:
    # Preferences and reopening the dialog are not ways out; only End engagement is (#490).
    window = _window(qtbot, _configured(tmp_path), on_persist=lambda config: None)
    _settle(worker_jobs)
    window._recon_source = InMemoryReconSource(engagements=[_ENGAGEMENT])
    window.import_engagement("R26-0004")
    dialog = window.open_chip_search()
    window.open_preferences().accept()  # Preferences OK rebuilds sources, not the engagement
    dialog.close()
    assert window.open_chip_search() is dialog
    assert window.active_engagement == "R26-0004"
    assert window.may_link_out() is False
    assert dialog.web_label.text() == LINK_OUT_LOCKED_BY_ENGAGEMENT


def test_ending_the_engagement_turns_the_link_outs_back_on(qtbot, tmp_path, worker_jobs) -> None:
    """The repair after lunch gets its web lookups back without a restart (#490)."""
    window = _window(qtbot, _configured(tmp_path))
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog.query_edit.setText("X1=")
    window._recon_source = InMemoryReconSource(engagements=[_ENGAGEMENT])
    window.import_engagement("R26-0004")
    assert not any(button.isEnabled() for button in dialog.site_buttons)

    assert window.end_engagement() == "R26-0004"

    assert window.may_link_out() is True
    # Straight away, with the dialog still open and nothing typed or clicked.
    assert any(button.isEnabled() for button in dialog.site_buttons)
    assert dialog.web_label.text() != LINK_OUT_LOCKED_BY_ENGAGEMENT


def test_importing_again_after_ending_locks_them_again(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, _configured(tmp_path))
    _settle(worker_jobs)
    dialog = window.open_chip_search()
    dialog.query_edit.setText("X1=")
    window._recon_source = InMemoryReconSource(engagements=[_ENGAGEMENT])
    window.import_engagement("R26-0004")
    window.end_engagement()
    window.import_engagement("R26-0004")
    assert window.may_link_out() is False
    assert not any(button.isEnabled() for button in dialog.site_buttons)
    assert dialog.web_label.text() == LINK_OUT_LOCKED_BY_ENGAGEMENT


def test_the_lock_message_says_where_to_end_it() -> None:
    assert "Engagement panel" in LINK_OUT_LOCKED_BY_ENGAGEMENT
    assert "restart" not in LINK_OUT_LOCKED_BY_ENGAGEMENT


def test_looking_chips_up_in_your_own_data_is_not_locked(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, _configured(tmp_path))
    _settle(worker_jobs)
    window._recon_source = InMemoryReconSource(engagements=[_ENGAGEMENT])
    window.import_engagement("R26-0004")
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "X1=")
    assert _part_numbers(dialog) == ["EX1234QFN", "EX9000SOT"]


# --- housekeeping -------------------------------------------------------------------


def test_the_package_exports_the_datasheet_library_and_the_finder() -> None:
    for name in (
        "DatasheetLibrary",
        "DatasheetFile",
        "ChipDocument",
        "MatchedBy",
        "Candidate",
        "find_chips",
    ):
        assert name in chips_package.__all__
        assert getattr(chips_package, name) is not None
    assert chips_package.DatasheetLibrary is DatasheetLibrary
    assert chips_package.Candidate is Candidate


def test_the_chips_doc_is_linked_and_names_every_site_it_can_send_a_mark_to() -> None:
    doc = (REPO / "docs" / "CHIPS.md").read_text(encoding="utf-8")
    assert "docs/CHIPS.md" in (REPO / "README.md").read_text(encoding="utf-8")
    assert "CHIPS.md" in (REPO / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for site in MARKING_SITES:
        assert f"`{site.host}`" in doc  # a site added to the code is a site the doc must own up to
    assert "stays active until you end it" in doc
    assert "End engagement…" in doc
    assert "on_air = false" in doc  # the switch #482 asked for, in place of its caveat
