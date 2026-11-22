"""Tests for the Find chip dialog (offscreen Qt, no files, nothing opened).

Every chip here is made up — "Example Semi" and "Nowhere Micro" don't exist
and neither do their parts, markings or documents.

Most tests run the dialog's workers inline, so a lookup is finished by the
time ``_type`` returns. The ones about threads use the real executor and wait.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtGui import Qt as QtGuiNamespace
from PySide6.QtWidgets import QPushButton

from benchhud.chips import find as chips_find
from benchhud.chips.marking import MarkingMatch
from benchhud.chips.model import Chip, Document, Marking, Substitute, UsedIn
from benchhud.chips.source import InMemoryChipSource, NullChipSource
from benchhud.shell import chip_dialog
from benchhud.shell.chip_dialog import (
    NO_DATA,
    NO_OPENER,
    NOT_PICKED,
    NOT_PICKED_ENTER,
    PROMPT,
    FindChipDialog,
    OpenOutcome,
)

_DATASHEET = Document("datasheet", "EX1234.pdf", 14, ("0123456789abcdef" * 4,))
_REF_DESIGN = Document("example-circuit", "EX1234-ref.pdf", 1, note="standby rail")

_BUCK = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    family="EX1234",
    package="QFN-20 (3x3)",
    package_family="QFN",
    functions=("buck-controller",),
    description="Single-phase buck controller for a standby rail",
    sources=("bench-notes",),
    markings=(Marking("X1=", ("X1=6A", "X1=7C"), "date code follows the ="),),
    documents=(_DATASHEET, _REF_DESIGN),
    substitutes=(Substitute("EX1235QFN", "check the enable threshold first"),),
    used_in=(UsedIn("EXAMPLE-BOARD-1", "U7000", "standby 3.3 V"),),
)
# Same mark as _BUCK, on a different chip in a different package.
_LDO = Chip(
    mpn="EX9000SOT",
    manufacturer="Example Semi",
    package="SOT-23-5",
    functions=("ldo",),
    description="150 mA low-dropout regulator",
    markings=(Marking("X1=", ("X1=2B",)),),
)
_SUB = Chip(
    mpn="EX1235QFN",
    manufacturer="Example Semi",
    package="QFN-20 (3x3)",
    functions=("buck-controller",),
    markings=(Marking("X1=6"),),
    documents=(Document("datasheet", "EX1235.pdf", 16),),
)
_SWITCH = Chip(
    mpn="NW4410DFN",
    manufacturer="Nowhere Micro",
    package="DFN-8 (2x2)",
    functions=("load-switch", "reverse-blocking"),
    markings=(Marking("X1=6AZ9"), Marking("NW4")),
)
_CHIPS = [_BUCK, _LDO, _SUB, _SWITCH]


def _inline(job) -> None:
    job()


def _dialog(qtbot, source=None, open_document=None, **kwargs) -> FindChipDialog:
    kwargs.setdefault("executor", _inline)
    source = InMemoryChipSource(list(_CHIPS)) if source is None else source
    dialog = FindChipDialog(source, open_document, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def _type(dialog: FindChipDialog, text: str) -> None:
    dialog.query_edit.setText(text)
    dialog.lookup_now()


def _rows(dialog: FindChipDialog) -> list[list[str]]:
    tree = dialog.tree
    return [
        [tree.topLevelItem(i).text(column) for column in range(tree.columnCount())]
        for i in range(tree.topLevelItemCount())
    ]


def _part_numbers(dialog: FindChipDialog) -> list[str]:
    return [row[0] for row in _rows(dialog)]


def _details(dialog: FindChipDialog) -> dict[str, list[tuple[str, str, str]]]:
    """The detail list, as ``{section heading: [row, ...]}``."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    for i in range(dialog.details.topLevelItemCount()):
        head = dialog.details.topLevelItem(i)
        out[head.text(0)] = [
            (head.child(j).text(0), head.child(j).text(1), head.child(j).text(2))
            for j in range(head.childCount())
        ]
    return out


def _detail_row(dialog: FindChipDialog, section: str, index: int = 0):
    for i in range(dialog.details.topLevelItemCount()):
        head = dialog.details.topLevelItem(i)
        if head.text(0) == section:
            return head.child(index)
    raise AssertionError(f"no section {section!r}")


class _Opener:
    """Stands in for "find the file and open it"; records how it was called."""

    def __init__(self, outcome: object = True) -> None:
        self.outcome = outcome
        self.calls: list[tuple[Chip, Document | None]] = []
        self.threads: list[threading.Thread] = []

    def __call__(self, chip: Chip, document: Document | None):
        self.calls.append((chip, document))
        self.threads.append(threading.current_thread())
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Queue(list):
    """An executor that keeps each job until the test runs it.

    Stands in for the worker thread where a test needs to say exactly when a
    lookup or an opener answers.
    """

    def __call__(self, job) -> None:
        self.append(job)

    def run_all(self) -> None:
        while self:
            self.pop(0)()


#: Whether Qt would render a string as markup if left to guess.
mightBeRichText = QtGuiNamespace.mightBeRichText


class _Recording(InMemoryChipSource):
    """An in-memory source that notes each lookup and the thread it ran on."""

    def __init__(self, chips: list[Chip]) -> None:
        super().__init__(chips)
        self.marks: list[str] = []
        self.threads: list[threading.Thread] = []

    def by_marking(self, text: str) -> list[MarkingMatch]:
        self.marks.append(text)
        self.threads.append(threading.current_thread())
        return super().by_marking(text)


# --- no data, no match ------------------------------------------------------


def test_no_chip_data_configured_says_so_and_looks_nothing_up(qtbot) -> None:
    class Counting(NullChipSource):
        calls = 0

        def get(self, mpn: str) -> Chip | None:
            Counting.calls += 1
            return None

        def by_marking(self, text: str) -> list[MarkingMatch]:
            Counting.calls += 1
            return []

    dialog = _dialog(qtbot, Counting())
    assert not dialog.notice_label.isHidden()
    assert dialog.notice_label.text() == NO_DATA

    _type(dialog, "X1=")
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert _rows(dialog) == []
    assert Counting.calls == 0
    assert not dialog.open_button.isEnabled()


def test_the_caller_can_say_no_chip_data_is_configured(qtbot) -> None:
    # A source that is merely empty for want of config — a directory source
    # with a blank path, a merge of no sources — isn't a NullChipSource.
    source = _Recording([])
    dialog = _dialog(qtbot, source, configured=False)
    assert not dialog.notice_label.isHidden()
    _type(dialog, "X1=")
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert source.marks == []
    assert dialog.status_label.text() == ""  # not "No chip matches"


def test_a_source_with_data_shows_no_notice(qtbot) -> None:
    dialog = _dialog(qtbot)
    assert dialog.notice_label.isHidden()
    assert dialog.status_label.text() == PROMPT


def test_no_match_says_so_plainly(qtbot) -> None:
    dialog = _dialog(qtbot, InMemoryChipSource([]))
    _type(dialog, "ZZ9")
    assert _rows(dialog) == []
    assert dialog.notice_label.isHidden()  # there is data; this just isn't in it
    assert "No chip matches “ZZ9”" in dialog.status_label.text()
    assert "in full" in dialog.status_label.text()
    assert dialog.selected() is None
    assert _details(dialog) == {}


def test_clearing_the_box_clears_the_list(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1=")
    assert _rows(dialog)
    dialog.query_edit.clear()
    assert _rows(dialog) == []
    assert dialog.status_label.text() == PROMPT
    assert dialog.selected() is None


# --- candidates -------------------------------------------------------------


def test_one_match_is_listed_and_shown(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "NW4")
    assert _rows(dialog) == [
        [
            "NW4410DFN",
            "Nowhere Micro",
            "DFN-8 (2x2)",
            "load-switch, reverse-blocking",
            "NW4",
            "exact",
        ]
    ]
    assert dialog.status_label.text() == "1 candidate."
    # Nothing to choose between, so its details are up without a keypress.
    assert dialog.selected().chip is _SWITCH
    assert dialog.title_label.text() == "NW4410DFN — Nowhere Micro"


def test_several_matches_are_ranked_and_none_is_picked(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1=6A")
    # Longest code first; the two chips that share "X1=" are both there, told
    # apart by package and function; the half-typed code is last.
    assert _rows(dialog) == [
        ["EX1235QFN", "Example Semi", "QFN-20 (3x3)", "buck-controller", "X1=6", "whole mark"],
        ["EX1234QFN", "Example Semi", "QFN-20 (3x3)", "buck-controller", "X1=", "whole mark"],
        ["EX9000SOT", "Example Semi", "SOT-23-5", "ldo", "X1=", "whole mark"],
        [
            "NW4410DFN",
            "Nowhere Micro",
            "DFN-8 (2x2)",
            "load-switch, reverse-blocking",
            "X1=6AZ9",
            "still typing",
        ],
    ]
    assert dialog.selected() is None
    assert dialog.tree.selectedItems() == []
    assert dialog.title_label.isHidden()
    assert dialog.found_label.text() == NOT_PICKED
    assert _details(dialog) == {}
    assert dialog.status_label.text() == "4 candidates, best match first."


def test_how_a_mark_matched_is_labelled(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1=")
    assert {row[0]: row[5] for row in _rows(dialog)} == {
        "EX1234QFN": "exact",
        "EX9000SOT": "exact",
        "EX1235QFN": "still typing",
        "NW4410DFN": "still typing",
    }


def test_a_full_part_number_is_found(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "ex9000sot")
    assert _rows(dialog) == [["EX9000SOT", "Example Semi", "SOT-23-5", "ldo", "—", "part number"]]
    assert dialog.selected().by_part_number
    assert dialog.found_label.text() == "Found by its part number."


def test_a_part_number_hit_is_listed_above_a_mark_that_also_matches(qtbot) -> None:
    clash = Chip(mpn="EX0001DFN", manufacturer="Example Semi", markings=(Marking("EX1234QFN"),))
    dialog = _dialog(qtbot, InMemoryChipSource([clash, *_CHIPS]))
    _type(dialog, "EX1234QFN")
    assert [(row[0], row[5]) for row in _rows(dialog)] == [
        ("EX1234QFN", "part number"),
        ("EX0001DFN", "exact"),
    ]
    assert dialog.selected() is None  # two candidates: still the operator's call


def test_a_chip_found_both_ways_is_one_row_that_says_both(qtbot) -> None:
    big = Chip(mpn="EX7700BGA", manufacturer="Example Semi", markings=(Marking("EX7700"),))
    dialog = _dialog(qtbot, InMemoryChipSource([big]))
    _type(dialog, "EX7700BGA")
    assert [(row[0], row[4], row[5]) for row in _rows(dialog)] == [
        ("EX7700BGA", "EX7700", "part number + whole mark")
    ]
    assert dialog.found_label.text() == (
        "Found by its part number.  "
        "Found by the mark EX7700 — what was typed starts with this code."
    )


def test_a_long_list_is_cut_off_and_says_so(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(chip_dialog, "MAX_SHOWN", 3)
    chips = [
        Chip(mpn=f"EX{n:04d}", manufacturer="Example Semi", markings=(Marking(f"X{n:04d}"),))
        for n in range(10)
    ]
    dialog = _dialog(qtbot, InMemoryChipSource(chips))
    _type(dialog, "X")
    assert _part_numbers(dialog) == ["EX0000", "EX0001", "EX0002"]
    assert dialog.status_label.text() == "Showing the best 3 of 10 candidates — keep typing."


def test_the_pick_is_kept_while_typing_on(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(_part_numbers(dialog).index("EX9000SOT")))
    _type(dialog, "X1=")
    assert dialog.selected().chip is _LDO
    _type(dialog, "NW4")  # it's no longer a candidate, and the one left is shown
    assert dialog.selected().chip is _SWITCH


# --- the detail pane --------------------------------------------------------


def test_picking_a_candidate_fills_the_detail_pane(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1=6A")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))

    assert dialog.title_label.text() == "EX1234QFN — Example Semi"
    assert dialog.facts_label.text() == "QFN-20 (3x3)  ·  buck-controller  ·  family EX1234"
    assert dialog.description_label.text() == "Single-phase buck controller for a standby rail"
    assert dialog.found_label.text() == (
        "Found by the mark X1= — what was typed starts with this code.  Credited to bench-notes."
    )
    assert _details(dialog) == {
        "Markings": [("X1=", "X1=6A, X1=7C", "date code follows the =")],
        "Documents": [
            ("datasheet", "EX1234.pdf", "14 pages"),
            ("example-circuit", "EX1234-ref.pdf", "1 page — standby rail"),
        ],
        "Substitutes": [("EX1235QFN", "check the enable threshold first", "")],
        "Seen on": [("EXAMPLE-BOARD-1", "U7000", "standby 3.3 V")],
    }


def test_the_detail_pane_follows_the_pick(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1=6A")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(2))

    assert dialog.title_label.text() == "EX9000SOT — Example Semi"
    assert dialog.facts_label.text() == "SOT-23-5  ·  ldo"
    assert dialog.description_label.text() == "150 mA low-dropout regulator"
    # What a chip doesn't list is said, not left out.
    assert _details(dialog) == {
        "Markings": [("X1=", "X1=2B", "")],
        "Documents — none listed": [],
        "Substitutes — none listed": [],
        "Seen on — none listed": [],
    }


def test_a_mark_typed_in_another_case_says_so(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "nw4")
    assert dialog.found_label.text() == (
        "Found by the mark NW4 — the code is exactly what was typed, in a different case."
    )


def test_markup_in_the_data_is_shown_as_text(qtbot) -> None:
    markup = '<img src="file:///nope.png"> &amp; <a href="x">link</a>'
    sly = Chip(
        mpn="EX<b>1</b>",
        manufacturer="<i>Example</i> Semi",
        package="<u>QFN</u>",
        description=markup,
        sources=("<b>notes</b>",),
        markings=(Marking("Z9", ("<b>Z9</b>",), markup),),
        documents=(Document("<b>datasheet</b>", "<b>a</b>.pdf", 1, note=markup),),
        substitutes=(Substitute("<b>EX2</b>", markup),),
        used_in=(UsedIn("<b>BOARD</b>", "<b>U1</b>", markup),),
    )
    bare = Chip(mpn="<b>EX3</b>", manufacturer="<i>Example</i>", markings=(Marking("Z8"),))
    opener = _Opener(OpenOutcome(True, "<b>done</b>"))
    dialog = _dialog(qtbot, InMemoryChipSource([sly, bare]), opener)
    labels = (
        dialog.title_label,
        dialog.facts_label,
        dialog.description_label,
        dialog.found_label,
        dialog.status_label,
        dialog.open_label,
    )

    def every_tooltip() -> list[str]:
        tips = [dialog.open_button.toolTip()]
        for i in range(dialog.details.topLevelItemCount()):
            head = dialog.details.topLevelItem(i)
            tips += [head.child(j).toolTip(c) for j in range(head.childCount()) for c in range(3)]
        return tips

    for typed in ("Z9", "Z8", "<b>EX3</b>", "<i>nothing</i>"):
        _type(dialog, typed)
        dialog.open_selected()
        # A label shows its text as written only when it's told to: left to
        # guess, Qt renders anything that looks like markup. A tooltip always
        # guesses, so no tooltip may carry the chip's own words.
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in labels)
        assert not any(mightBeRichText(tip) for tip in every_tooltip()), every_tooltip()

    assert [chip.mpn for chip, _ in opener.calls] == ["EX<b>1</b>"]
    _type(dialog, "Z9")
    assert dialog.title_label.text() == "EX<b>1</b> — <i>Example</i> Semi"
    assert dialog.description_label.text() == markup
    dialog.open_selected()
    assert dialog.open_label.text() == "<b>done</b>"


def test_one_long_unbroken_string_does_not_size_the_dialog(qtbot) -> None:
    wide = Chip(
        mpn="EX" + "9" * 400,
        manufacturer="M" * 800,
        package="P" * 800,
        description="D" * 3000,
        sources=("S" * 800,),
        markings=(Marking("Z9"),),
    )
    dialog = _dialog(qtbot, InMemoryChipSource([wide]))
    before = dialog.minimumSizeHint().width()
    _type(dialog, "Z9")
    assert dialog.selected().chip is wide
    assert dialog.minimumSizeHint().width() == before
    assert dialog.query_edit.maxLength() == chip_dialog.MAX_TYPED


# --- what's picked ------------------------------------------------------------


def test_a_row_shown_for_being_the_only_one_is_let_go_when_the_list_grows(qtbot) -> None:
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "NW4")
    assert dialog.selected().chip is _SWITCH  # the only candidate, so it's shown

    _type(dialog, "X1=6A")  # now it's one of four, and the worst-ranked of them
    assert "NW4410DFN" in _part_numbers(dialog)
    assert dialog.selected() is None
    assert dialog.found_label.text() == NOT_PICKED_ENTER

    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert dialog.selected().chip is _SUB  # Enter picks the top row; it opens nothing yet
    assert opener.calls == []


def test_a_row_the_operator_picked_is_kept_even_when_it_was_the_only_one(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "NW4")
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)  # already on it: nothing moves
    _type(dialog, "X1=6A")
    assert dialog.selected() is None

    _type(dialog, "X1=6A ")  # same text once trimmed: nothing is looked up again
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(3))
    _type(dialog, "X1=")
    assert dialog.selected().chip is _SWITCH  # picked by hand, so it follows


# --- keyboard ---------------------------------------------------------------


def test_arrows_move_through_the_candidates_from_the_box(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1=6A")
    box = dialog.query_edit

    qtbot.keyClick(box, Qt.Key.Key_Down)
    assert dialog.selected().chip is _SUB
    qtbot.keyClick(box, Qt.Key.Key_Down)
    assert dialog.selected().chip is _BUCK
    qtbot.keyClick(box, Qt.Key.Key_Up)
    assert dialog.selected().chip is _SUB
    qtbot.keyClick(box, Qt.Key.Key_Up)  # already at the top
    assert dialog.selected().chip is _SUB
    qtbot.keyClick(box, Qt.Key.Key_PageDown)
    assert dialog.selected().chip is _SWITCH
    qtbot.keyClick(box, Qt.Key.Key_Up)
    assert dialog.selected().chip is _LDO
    qtbot.keyClick(box, Qt.Key.Key_PageUp)
    assert dialog.selected().chip is _SUB
    assert box.text() == "X1=6A"  # the arrows didn't touch the text


@pytest.mark.parametrize("enter", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_type_arrow_enter_opens_the_datasheet(qtbot, enter) -> None:
    opener = _Opener(OpenOutcome(True, "Opened EX1235.pdf."))
    dialog = _dialog(qtbot, open_document=opener)
    dialog.show()
    qtbot.keyClicks(dialog.query_edit, "X1=6A")
    dialog.lookup_now()
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)
    qtbot.keyClick(dialog.query_edit, enter)

    assert opener.calls == [(_SUB, None)]
    assert dialog.open_label.text() == "Opened EX1235.pdf."
    assert dialog.isVisible()


def test_no_button_takes_enter_for_itself(qtbot) -> None:
    dialog = _dialog(qtbot, open_document=_Opener())
    dialog.show()
    buttons = dialog.findChildren(QPushButton)
    assert {button.text() for button in buttons} >= {"Open datasheet", "Close"}
    assert not any(button.isDefault() or button.autoDefault() for button in buttons)
    # Enter that reaches the dialog itself — from nowhere in particular — closes nothing.
    qtbot.keyClick(dialog, Qt.Key.Key_Return)
    assert dialog.isVisible()


def test_enter_with_nothing_picked_picks_the_first_and_opens_nothing(qtbot) -> None:
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "X1=6A")
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert dialog.selected().chip is _SUB
    assert opener.calls == []
    qtbot.keyClick(dialog.tree, Qt.Key.Key_Return)  # from the list this time
    assert opener.calls == [(_SUB, None)]


def test_enter_never_acts_on_a_list_that_answers_older_text(qtbot) -> None:
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "NW4")
    assert dialog.selected().chip is _SWITCH

    dialog.query_edit.setText("ex1235qfn")  # typed, and Enter before the pause is up
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert opener.calls == []  # it looked the new text up instead
    assert _part_numbers(dialog) == ["EX1235QFN"]

    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert opener.calls == [(_SUB, None)]


def test_an_arrow_never_acts_on_a_list_that_answers_older_text(qtbot) -> None:
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "X1=")
    assert _part_numbers(dialog)[0] == "EX1234QFN"  # top of the list for "X1="

    dialog.query_edit.setText("X1=6A")  # typed on, and ↓ before the pause is up
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)
    # ↓ landed on the top of the list for what's typed now, not the old top.
    assert _part_numbers(dialog)[0] == "EX1235QFN"
    assert dialog.selected().chip is _SUB
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert opener.calls == [(_SUB, None)]


def test_arrows_pressed_while_a_lookup_is_out_land_on_the_list_it_brings(qtbot) -> None:
    jobs = _Queue()
    dialog = _dialog(qtbot, executor=jobs)
    dialog.query_edit.setText("X1=6A")
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)  # the lookup starts, and is out
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)
    assert _rows(dialog) == []
    jobs.run_all()
    assert _part_numbers(dialog)[1] == "EX1234QFN"
    assert dialog.selected().chip is _BUCK  # two presses: the second row


def test_an_arrow_pressed_before_more_typing_is_forgotten(qtbot) -> None:
    jobs = _Queue()
    dialog = _dialog(qtbot, executor=jobs)
    dialog.query_edit.setText("X1=6A")
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)  # held for the list that's coming
    dialog.query_edit.setText("X1=6")  # …but the operator typed on instead
    jobs.run_all()
    dialog.lookup_now()
    jobs.run_all()
    assert len(_rows(dialog)) == 4
    assert dialog.selected() is None  # the arrow belonged to text that's gone


def test_holding_enter_opens_once(qtbot) -> None:
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "EX1234QFN")
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    for _ in range(5):  # the key is still down
        repeat = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, "\r", True
        )
        QCoreApplication.sendEvent(dialog.query_edit, repeat)
    assert opener.calls == [(_BUCK, None)]


def test_typing_with_a_list_focused_goes_to_the_box(qtbot) -> None:
    dialog = _dialog(qtbot)
    dialog.show()
    _type(dialog, "X1=")
    dialog.tree.setFocus()
    qtbot.keyClicks(dialog.tree, "6A")
    assert dialog.query_edit.text() == "X1=6A"
    assert dialog.focusWidget() is dialog.query_edit
    dialog.lookup_now()
    assert dialog.selected() is None  # the list's own type-to-search didn't move the pick

    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)
    dialog.details.setFocus()
    qtbot.keyClicks(dialog.details, "Z")
    assert dialog.query_edit.text() == "X1=6AZ"


def test_enter_on_a_substitute_looks_it_up_and_undo_goes_back(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "EX1234QFN")
    dialog.details.setCurrentItem(_detail_row(dialog, "Substitutes"))
    qtbot.keyClick(dialog.details, Qt.Key.Key_Return)
    assert dialog.query_edit.text() == "EX1235QFN"
    assert dialog.selected().chip is _SUB

    dialog.query_edit.undo()
    dialog.lookup_now()
    assert dialog.query_edit.text() == "EX1234QFN"
    assert dialog.selected().chip is _BUCK


def test_a_double_click_opens(qtbot) -> None:
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "EX1234QFN")
    dialog.tree.itemDoubleClicked.emit(dialog.tree.topLevelItem(0), 0)
    assert opener.calls == [(_BUCK, None)]
    dialog.details.itemDoubleClicked.emit(_detail_row(dialog, "Documents", 1), 0)
    assert opener.calls == [(_BUCK, None), (_BUCK, _REF_DESIGN)]


def test_showing_the_dialog_puts_the_box_ready_to_type_over(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "X1=")
    dialog.tree.setFocus()
    assert dialog.focusWidget() is dialog.tree
    with qtbot.waitExposed(dialog):
        dialog.show()
    assert dialog.focusWidget() is dialog.query_edit
    assert dialog.query_edit.selectedText() == "X1="


# --- opening a document -----------------------------------------------------


def test_open_is_off_with_a_reason_when_nothing_can_open_documents(qtbot) -> None:
    dialog = _dialog(qtbot)
    assert not dialog.open_label.isHidden()
    assert dialog.open_label.text() == NO_OPENER  # on the dialog, not only in a tooltip

    _type(dialog, "EX1234QFN")
    assert dialog.selected().chip is _BUCK
    assert not dialog.open_button.isEnabled()
    assert dialog.open_button.toolTip() == NO_OPENER

    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert dialog.open_selected() is False
    # A document row is no way round it either.
    dialog.details.setCurrentItem(_detail_row(dialog, "Documents"))
    qtbot.keyClick(dialog.details, Qt.Key.Key_Return)
    assert dialog.open_label.text() == NO_OPENER
    assert not dialog._opening

    _type(dialog, "X1=6A")  # and the hint doesn't promise an Enter that does nothing
    assert dialog.found_label.text() == NOT_PICKED


def test_the_caller_can_say_why_nothing_opens(qtbot) -> None:
    dialog = _dialog(qtbot, open_unavailable="Set a datasheet folder in Preferences.")
    assert dialog.open_label.text() == "Set a datasheet folder in Preferences."


def test_open_is_on_once_a_candidate_with_documents_is_picked(qtbot) -> None:
    dialog = _dialog(qtbot, open_document=_Opener())
    assert dialog.open_label.text() == ""
    assert not dialog.open_button.isEnabled()  # nothing picked yet
    assert dialog.open_button.toolTip() == "Pick a candidate first."
    assert dialog.open_selected() is False
    assert dialog.open_label.text() == "Pick a candidate first."

    _type(dialog, "EX1234QFN")
    assert dialog.open_button.isEnabled()
    assert dialog.open_label.text() == ""

    _type(dialog, "EX9000SOT")  # lists no documents at all
    assert not dialog.open_button.isEnabled()
    assert dialog.open_button.toolTip() == "No documents are listed for this chip."
    assert dialog.open_selected() is False
    assert dialog.open_label.text() == "No documents are listed for this chip."
    assert dialog.open_label.property("tone") == "warning"


def test_the_button_opens_the_picked_chips_datasheet(qtbot) -> None:
    opener = _Opener(OpenOutcome(True, "Opened EX1234.pdf — the fingerprinted revision."))
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "EX1234QFN")
    dialog.open_button.click()
    assert opener.calls == [(_BUCK, None)]
    assert dialog.open_label.text() == "Opened EX1234.pdf — the fingerprinted revision."
    assert dialog.open_label.property("tone") == "ok"
    assert dialog.status_label.text() == "1 candidate."  # the lookup line is left alone
    assert dialog.open_button.isEnabled()  # ready for the next one


def test_enter_on_a_document_row_opens_that_document(qtbot) -> None:
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener)
    _type(dialog, "EX1234QFN")
    dialog.details.setCurrentItem(_detail_row(dialog, "Documents", 1))
    qtbot.keyClick(dialog.details, Qt.Key.Key_Return)
    assert opener.calls == [(_BUCK, _REF_DESIGN)]
    assert dialog.open_label.text() == "Opened EX1234-ref.pdf."


@pytest.mark.parametrize(
    ("outcome", "said", "tone"),
    [
        (True, "Opened the datasheet for EX1234QFN.", "ok"),
        (False, "No file found for the datasheet for EX1234QFN.", "warning"),
        (None, "No file found for the datasheet for EX1234QFN.", "warning"),
        (OpenOutcome(False, "EX1234.pdf isn't in ~/datasheets."), "EX1234.pdf isn't in", "warning"),
        (OSError("no handler"), "Couldn't open the datasheet for EX1234QFN", "error"),
        (SystemExit(3), "Couldn't open the datasheet for EX1234QFN", "error"),
    ],
)
def test_what_the_opener_did_is_said(qtbot, outcome, said, tone) -> None:
    dialog = _dialog(qtbot, open_document=_Opener(outcome))
    _type(dialog, "EX1234QFN")
    assert dialog.open_selected() is True  # an opener that raises doesn't raise here
    assert dialog.open_label.text().startswith(said)
    assert dialog.open_label.property("tone") == tone
    assert dialog.open_button.isEnabled()  # whatever it did, the button is back


def test_a_lookup_and_an_open_do_not_talk_over_each_other(qtbot) -> None:
    jobs = _Queue()
    opener = _Opener(OpenOutcome(False, "EX1234.pdf isn't in the folder."))
    dialog = _dialog(qtbot, open_document=opener, executor=jobs)
    dialog.query_edit.setText("EX1234QFN")
    dialog.lookup_now()
    jobs.run_all()
    assert dialog.open_selected() is True  # the opener is out…
    dialog.query_edit.setText("NW4")  # …and the operator is on to the next mark
    dialog.lookup_now()
    lookup = jobs.pop(1)
    lookup()
    assert dialog.status_label.text() == "1 candidate."
    assert dialog.open_label.text() == "Looking for the datasheet for EX1234QFN…"

    jobs.run_all()  # the opener answers, late
    assert dialog.open_label.text() == "EX1234.pdf isn't in the folder."
    assert dialog.open_label.property("tone") == "warning"
    assert dialog.status_label.text() == "1 candidate."

    dialog.query_edit.setText("X1=6A")  # another chip is picked: that's old news now
    dialog.lookup_now()
    jobs.run_all()
    assert dialog.open_label.text() == ""


def test_after_an_open_the_next_mark_types_over_the_last(qtbot) -> None:
    jobs = _Queue()
    dialog = _dialog(qtbot, open_document=_Opener(), executor=jobs)
    dialog.query_edit.setText("EX1234QFN")
    dialog.lookup_now()
    jobs.run_all()
    dialog.query_edit.deselect()
    dialog.open_selected()
    jobs.run_all()
    assert dialog.query_edit.selectedText() == "EX1234QFN"

    dialog.query_edit.deselect()
    dialog.open_selected()
    dialog.query_edit.setText("EX1234QFN ")  # typing again before the opener answers
    jobs.run_all()
    assert dialog.query_edit.selectedText() == ""  # what's being typed is left alone


def test_the_opener_never_runs_on_the_gui_thread(qtbot) -> None:
    release = threading.Event()
    started = threading.Event()

    class Slow(_Opener):
        def __call__(self, chip, document):
            started.set()
            assert release.wait(10)  # hashing a folder, say
            return super().__call__(chip, document)

    opener = Slow(OpenOutcome(True, "Opened EX1234.pdf."))
    dialog = FindChipDialog(_Recording(list(_CHIPS)), opener)  # the real executor
    qtbot.addWidget(dialog)
    dialog.query_edit.setText("EX1234QFN")
    dialog.lookup_now()
    qtbot.waitUntil(lambda: dialog.selected() is not None, timeout=5000)

    assert dialog.open_selected() is True  # returned while the opener still waits
    assert started.wait(5)
    try:
        assert dialog.open_label.text() == "Looking for the datasheet for EX1234QFN…"
        # One at a time, and the reason is there to read.
        assert not dialog.open_button.isEnabled()
        assert dialog.open_selected() is False
        assert dialog.open_label.text() == "Still looking for the last one."
        # The GUI thread is free: a lookup still goes through meanwhile.
        dialog.query_edit.setText("NW4")
        qtbot.waitUntil(lambda: _part_numbers(dialog) == ["NW4410DFN"], timeout=5000)
    finally:
        release.set()
    qtbot.waitUntil(lambda: dialog.open_label.text() == "Opened EX1234.pdf.", timeout=5000)

    assert opener.calls == [(_BUCK, None)]
    assert opener.threads[0] is not threading.main_thread()


# --- lookups stay off the GUI thread ----------------------------------------


def test_lookups_run_off_the_gui_thread(qtbot) -> None:
    source = _Recording(list(_CHIPS))
    dialog = FindChipDialog(source)  # the real executor, the real pause
    qtbot.addWidget(dialog)
    dialog.query_edit.setText("X1=6A")
    assert _rows(dialog) == []  # nothing happened inside the keystroke
    assert source.marks == []
    qtbot.waitUntil(lambda: len(_rows(dialog)) == 4, timeout=5000)
    assert source.marks == ["X1=6A"]
    assert source.threads[0] is not threading.main_thread()


def test_nothing_is_looked_up_until_typing_pauses(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(chip_dialog, "DEBOUNCE_MS", 60_000)  # a pause that never comes
    jobs = _Queue()
    source = _Recording(list(_CHIPS))
    dialog = _dialog(qtbot, source, executor=jobs)
    for text in ("X", "X1", "X1="):
        dialog.query_edit.setText(text)
    qtbot.wait(50)
    assert len(jobs) == 0  # still typing, as far as the dialog knows

    dialog.lookup_now()  # what the pause does when it comes
    jobs.run_all()
    assert source.marks == ["X1="]  # one lookup, for the text as it ended up


def test_a_burst_of_typing_is_one_lookup(qtbot) -> None:
    source = _Recording(list(_CHIPS))
    dialog = FindChipDialog(source)  # the real executor, the real pause
    qtbot.addWidget(dialog)
    for text in ("X", "X1", "X1=", "X1=6", "X1=6A"):
        dialog.query_edit.setText(text)
    qtbot.waitUntil(lambda: len(_rows(dialog)) == 4, timeout=5000)
    assert source.marks == ["X1=6A"]


def test_a_slow_source_never_blocks_typing_and_the_newest_text_wins(qtbot) -> None:
    release = threading.Event()
    started = threading.Event()
    asked: list[str] = []

    class Slow(_Recording):
        def by_marking(self, text: str) -> list[MarkingMatch]:
            asked.append(text)
            started.set()
            assert release.wait(10)  # a first read of a big checkout, say
            return super().by_marking(text)

    dialog = FindChipDialog(Slow(list(_CHIPS)))
    qtbot.addWidget(dialog)
    dialog.query_edit.setText("X")
    dialog.lookup_now()
    assert started.wait(5)
    try:
        # Typing goes on while the first lookup is still out.
        for text in ("X1", "X1=", "NW4"):
            dialog.query_edit.setText(text)
            dialog.lookup_now()
        assert _rows(dialog) == []
        assert asked == ["X"]  # one lookup at a time: nothing piled up behind it
    finally:
        release.set()
    qtbot.waitUntil(lambda: _part_numbers(dialog) == ["NW4410DFN"], timeout=5000)
    # The answer to "X" was dropped, and the texts in between were never asked.
    assert asked == ["X", "NW4"]


def test_a_lookup_that_takes_a_while_says_it_is_looking(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(chip_dialog, "SLOW_MS", 1)
    jobs = _Queue()
    dialog = _dialog(qtbot, executor=jobs)
    dialog.query_edit.setText("X1=")
    dialog.lookup_now()
    qtbot.waitUntil(lambda: dialog.status_label.text() == "Looking up “X1=”…", timeout=5000)
    jobs.run_all()
    assert dialog.status_label.text() == "4 candidates, best match first."


def test_looking_is_not_left_on_the_line_when_the_answer_is_dropped(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(chip_dialog, "SLOW_MS", 1)
    jobs = _Queue()
    dialog = _dialog(qtbot, executor=jobs)
    dialog.query_edit.setText("NW4")
    dialog.lookup_now()
    jobs.run_all()
    assert dialog.status_label.text() == "1 candidate."

    dialog.query_edit.setText("NW4X")  # one character too many…
    dialog.lookup_now()
    qtbot.waitUntil(lambda: dialog.status_label.text() == "Looking up “NW4X”…", timeout=5000)
    dialog.query_edit.setText("NW4")  # …taken back before the lookup answers
    jobs.run_all()
    assert _part_numbers(dialog) == ["NW4410DFN"]
    assert dialog.status_label.text() == "1 candidate."

    dialog.query_edit.setText("X1=")
    dialog.lookup_now()
    dialog.query_edit.clear()  # cleared while that one is still out
    qtbot.wait(30)  # long enough for "still looking" to have fired
    assert dialog.status_label.text() == PROMPT
    jobs.run_all()
    assert dialog.status_label.text() == PROMPT
    assert _rows(dialog) == []


def test_a_dialog_closed_and_shown_again_with_work_out_picks_up(qtbot) -> None:
    jobs = _Queue()
    opener = _Opener()
    dialog = _dialog(qtbot, open_document=opener, executor=jobs)
    dialog.show()
    dialog.query_edit.setText("EX1234QFN")
    dialog.lookup_now()
    jobs.run_all()
    dialog.open_selected()  # the opener is out…
    dialog.query_edit.setText("NW4")
    dialog.lookup_now()  # …and so is a lookup
    dialog.close()
    jobs.pop(1)()  # both answer a hidden dialog: the lookup,
    jobs.run_all()  # then the opener

    dialog.show()
    assert _part_numbers(dialog) == ["NW4410DFN"]
    assert dialog.open_label.text() == "Opened the datasheet for EX1234QFN."
    assert not dialog._looking
    assert not dialog._opening


# --- nothing raises into the HUD --------------------------------------------


def test_a_source_that_raises_is_a_message_and_enter_tries_again(qtbot, caplog) -> None:
    class Broken(InMemoryChipSource):
        broken = True

        def by_marking(self, text: str) -> list[MarkingMatch]:
            if self.broken:
                raise OSError("disk went away")
            return super().by_marking(text)

    source = Broken(list(_CHIPS))
    dialog = _dialog(qtbot, source)
    _type(dialog, "NW4")
    assert _rows(dialog) == []
    assert dialog.status_label.text() == (
        "The lookup failed — the log has the reason. Enter tries again."
    )
    assert dialog.status_label.property("tone") == "error"
    assert "disk went away" in caplog.text

    source.broken = False  # the disk is back; the text hasn't changed
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Return)
    assert _part_numbers(dialog) == ["NW4410DFN"]
    assert dialog.status_label.property("tone") is None


def test_a_worker_that_cannot_start_is_a_message_not_a_crash(qtbot) -> None:
    def no_threads(job) -> None:
        raise RuntimeError("can't start new thread")

    dialog = _dialog(qtbot, open_document=_Opener(), executor=no_threads)
    _type(dialog, "X1=")
    assert dialog.status_label.text().startswith("The lookup failed")
    assert not dialog._looking

    # Put an answer in by hand, since no lookup can run, and try to open it.
    dialog._show("EX1234QFN", chip_dialog.find_chips(InMemoryChipSource(list(_CHIPS)), "EX1234QFN"))
    assert dialog.open_selected() is False
    assert dialog.open_label.text().startswith("Couldn't open the datasheet for EX1234QFN")
    assert not dialog._opening


def test_a_record_the_list_cannot_draw_is_a_message_not_a_crash(qtbot, caplog) -> None:
    bad = Chip(
        mpn="EX0BAD",
        manufacturer="Example Semi",
        functions=("ldo", 5),  # a source that didn't check its types
        markings=(Marking("Q7"),),
    )
    dialog = _dialog(qtbot, InMemoryChipSource([bad, *_CHIPS]))
    _type(dialog, "Q7")
    assert _rows(dialog) == []
    assert dialog.status_label.text().startswith("The lookup failed")
    assert "could not show the answer" in caplog.text
    assert not dialog.tree.signalsBlocked()

    _type(dialog, "X1=6A")  # the list still works, and so does picking from it
    qtbot.keyClick(dialog.query_edit, Qt.Key.Key_Down)
    assert dialog.title_label.text() == "EX1235QFN — Example Semi"


def test_details_the_pane_cannot_draw_are_a_message_not_a_crash(qtbot, caplog) -> None:
    bad = Chip(
        mpn="EX0BAD",
        manufacturer="Example Semi",
        markings=(Marking("Q7", ("Q7A", 5)),),  # an example that isn't text
    )
    dialog = _dialog(qtbot, InMemoryChipSource([bad]))
    _type(dialog, "Q7")
    assert _part_numbers(dialog) == ["EX0BAD"]
    assert dialog.found_label.text().startswith("This chip's details can't be shown")
    assert _details(dialog) == {}
    assert "could not show a chip's details" in caplog.text


def test_an_answer_that_arrives_after_the_dialog_is_gone_is_dropped(qtbot) -> None:
    opener = _Opener()
    dialog = FindChipDialog(InMemoryChipSource(list(_CHIPS)), opener, executor=_inline)
    shiboken6.delete(dialog)
    # What a worker does when it finishes late: neither may raise.
    dialog._look_up("X1=")
    dialog._do_open(opener, _BUCK, None)
    assert opener.calls == [(_BUCK, None)]


# --- what the dialog is allowed to know about --------------------------------


def test_the_dialog_reads_chip_reference_and_nothing_else() -> None:
    """An operator dialog: no job, customer, config or output code comes near it.

    Every import of both modules is on this list. So it doesn't import the
    datasheet library — opening a document is handed in by whoever builds the
    dialog — nor anything that reaches the network or starts a process, and
    the only Qt modules are the three the rest of the app already uses.
    """
    allowed = {
        "__future__",
        "collections.abc",
        "contextlib",
        "dataclasses",
        "logging",
        "threading",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "benchhud.chips.find",
        "benchhud.chips.marking",
        "benchhud.chips.model",
        "benchhud.chips.source",
        "benchhud.shell.qt_util",
    }
    for module in (chip_dialog, chips_find):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, "a relative import hides what it reaches"
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert imported <= allowed, f"{module.__name__} imports {sorted(imported - allowed)}"
