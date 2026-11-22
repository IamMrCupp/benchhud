"""The chip reference panel — one chip as a card, stream-safe.

Hardware-free and offscreen. The panel looks a chip up on a worker; here the
executor is a stand-in that runs the job on the spot, or holds it so a test
can let the answers land in the order it wants. Every chip is made up.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QPushButton, QWidget

from benchhud.chips.composite import CompositeChipSource, Conflict, MergedChip
from benchhud.chips.dir_source import DirChipSource
from benchhud.chips.model import Chip, Document, Marking, Substitute, UsedIn
from benchhud.chips.source import InMemoryChipSource, NullChipSource
from benchhud.config import ChipSourceConfig, Config
from benchhud.jobs.store import JobStore
from benchhud.recon import Engagement, InMemoryReconSource
from benchhud.shell.dock_groups import group_of
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels import chip as chip_module
from benchhud.shell.panels.chip import (
    LEFT_OUT_FIELDS,
    MAX_DESCRIPTION,
    MAX_FUNCTIONS,
    MAX_LINE,
    MAX_MARKINGS,
    SHOWN_FIELDS,
    ChipCard,
    ChipPanel,
    Found,
    State,
    ValueLabel,
    card_for,
    look_up,
)
from benchhud.tickets.source import InMemoryTicketSource, Ticket

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "chip-data"

EX1234 = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    family="EX1234",
    package="QFN-20 (3x3)",
    package_family="QFN",
    functions=("buck-controller",),
    description="Single-phase buck controller for a standby rail",
    sources=("bench-notes",),
    markings=(Marking("X1=", ("X1=6A", "X1=7C"), "Top line. The second line is a date code."),),
    documents=(Document("datasheet", "EX1234.pdf", 14, ("0123456789abcdef" * 4,)),),
    substitutes=(Substitute("EX1235QFN", "Pin-compatible; check the enable threshold"),),
    used_in=(UsedIn("EXAMPLE-BOARD-1", "U7000", "standby 3.3 V"),),
)
EX9000 = Chip(
    mpn="EX9000SOT",
    manufacturer="Example Semi",
    package="SOT-23-5",
    functions=("ldo", "load-switch"),
    markings=(Marking("X1="), Marking("X9", ("X9B",))),
)


def _inline(job: Callable[[], None]) -> None:
    job()


class _Held:
    """An executor that keeps each job until the test runs it."""

    def __init__(self) -> None:
        self.jobs: list[Callable[[], None]] = []

    def __call__(self, job: Callable[[], None]) -> None:
        self.jobs.append(job)


def _panel(qtbot, source, executor=_inline) -> ChipPanel:
    panel = ChipPanel(lambda: source, executor=executor)
    qtbot.addWidget(panel)
    return panel


def _everything_drawn(widget: QWidget) -> str:
    """Every piece of text the panel could put on screen, in one string.

    Labels, buttons, the box and its placeholder, and every tooltip and
    accessible name — whatever a viewer, a hover or a screen grab could show.
    """
    bits: list[str] = []
    for child in [widget, *widget.findChildren(QWidget)]:
        bits += [child.toolTip(), child.statusTip(), child.whatsThis()]
        bits += [child.accessibleName(), child.accessibleDescription(), child.windowTitle()]
        if isinstance(child, QLabel | QAbstractButton):
            bits.append(child.text())
        if isinstance(child, QLineEdit):
            bits += [child.text(), child.placeholderText()]
    return "\n".join(bit for bit in bits if bit)


def _shown(label: QLabel) -> bool:
    return not label.isHidden()


# --- the card's words (no Qt) ----------------------------------------------


def test_the_card_says_what_the_chip_is() -> None:
    card = card_for(EX1234)
    assert card == ChipCard(
        title="EX1234QFN",
        maker="Example Semi · EX1234 family",
        package="QFN-20 (3x3)",
        functions="buck controller",
        markings="X1=",
        description="Single-phase buck controller for a standby rail",
    )


def test_a_bare_record_is_a_title_and_a_maker() -> None:
    card = card_for(Chip(mpn="SD77/NOPB", manufacturer="Sample Devices"))
    assert card == ChipCard(title="SD77/NOPB", maker="Sample Devices")


def test_several_functions_and_marks_are_listed() -> None:
    card = card_for(EX9000)
    assert card.functions == "ldo, load switch"
    assert card.markings == "X1=, X9"


def test_a_long_record_is_cut_to_fit() -> None:
    chip = Chip(
        mpn="E" * 200,
        manufacturer="M" * 200,
        package="P" * 200,
        functions=tuple(f"fn-{n}" for n in range(MAX_FUNCTIONS + 3)),
        markings=tuple(Marking(f"M{n}") for n in range(MAX_MARKINGS + 2)),
        description="word " * 200,
    )
    card = card_for(chip)
    assert len(card.title) == MAX_LINE and card.title.endswith("…")
    assert len(card.maker) == MAX_LINE and len(card.package) == MAX_LINE
    assert len(card.description) <= MAX_DESCRIPTION and card.description.endswith("…")
    assert card.functions.endswith("+3 more") and card.functions.count(",") == MAX_FUNCTIONS - 1
    assert card.markings.endswith("+2 more") and card.markings.count(",") == MAX_MARKINGS - 1


def test_line_breaks_in_a_value_become_spaces() -> None:
    card = card_for(Chip(mpn="EX1\nQFN", manufacturer="Example\r\n\tSemi", description="a\n\nb"))
    assert (card.title, card.maker, card.description) == ("EX1 QFN", "Example Semi", "a b")


def test_a_mangled_record_draws_blanks_not_reprs() -> None:
    chip = Chip(
        mpn="EX1",
        manufacturer=None,  # type: ignore[arg-type]
        package=42,  # type: ignore[arg-type]
        functions=("ldo", None, 7),  # type: ignore[arg-type]
        markings=(Marking("X1"), "not a marking", Marking(None)),  # type: ignore[arg-type]
        description=["a", "list"],  # type: ignore[arg-type]
    )
    card = card_for(chip)
    assert card == ChipCard(title="EX1", functions="ldo", markings="X1")


def test_a_dispute_names_the_value_and_nothing_else() -> None:
    assert card_for(EX1234, ("package",)).disputed == "Sources disagree on package."
    assert (
        card_for(EX1234, ("manufacturer", "description", "package")).disputed
        == "Sources disagree on maker, description, package."
    )
    assert card_for(EX1234).disputed == ""


def test_a_field_name_the_card_doesnt_know_is_never_printed() -> None:
    line = card_for(EX1234, ("customer: Acme", "package", "ticket 4471", None)).disputed  # type: ignore[arg-type]
    assert line == "Sources disagree on package."


def test_a_dispute_over_a_value_the_card_doesnt_show_isnt_mentioned() -> None:
    """``package_family`` can be disputed on its own, and it isn't on the card."""
    assert card_for(EX1234, ("package_family",)).disputed == ""
    assert card_for(EX1234, ("sources", "customer")).disputed == ""


def test_control_and_invisible_characters_are_dropped() -> None:
    """A text-direction override would reorder the card's own wording."""
    chip = Chip(
        mpn="EX\u202e1234\u2066QFN\u2069",
        manufacturer="Exa\u200bmple\x00 Se\x07mi\x1b",
        family="\ufeffEX\u00ad1234",
        package="QFN\u200d-20",
        functions=("buck\u200e-controller",),
        markings=(Marking("X\u202d1="),),
        description="A\u2028B\u2029C \ue000 D",
    )
    card = card_for(chip)
    assert card == ChipCard(
        title="EX1234QFN",
        maker="Example Semi · EX1234 family",
        package="QFN-20",
        functions="buck controller",
        markings="X1=",
        description="A B C D",
    )


# --- the lookup (no Qt) ----------------------------------------------------


def test_look_up_takes_the_merged_chip_when_the_source_merges() -> None:
    other = dataclasses.replace(EX1234, package="QFN-24 (4x4)")
    source = CompositeChipSource(
        [InMemoryChipSource([other], name="overlay"), InMemoryChipSource([EX1234], name="base")]
    )
    found = look_up(source, "ex1234qfn")
    assert found is not None
    assert found.chip.package == "QFN-24 (4x4)"  # the first source wins
    assert found.disputed == ("package",)


def test_look_up_asks_a_plain_source_for_the_chip() -> None:
    found = look_up(InMemoryChipSource([EX1234]), "EX1234QFN")
    assert found == Found(EX1234, ())
    assert look_up(InMemoryChipSource([EX1234]), "EX0000") is None
    assert look_up(CompositeChipSource([InMemoryChipSource([EX1234])]), "EX0000") is None


class _Rereading(InMemoryChipSource):
    """A source that reads from somewhere: ``reload`` swaps in what's there now."""

    def __init__(self, chips: list[Chip], on_disk: list[Chip] | None = None) -> None:
        super().__init__(list(chips))
        self.on_disk = list(chips) if on_disk is None else on_disk
        self.reloads = 0

    def reload(self) -> None:
        self.reloads += 1
        self.chips = list(self.on_disk)


def test_look_up_can_have_the_source_read_again_first() -> None:
    newer = dataclasses.replace(EX1234, package="QFN-24 (4x4)")
    source = _Rereading([EX1234], on_disk=[newer])
    assert look_up(source, "EX1234QFN") == Found(EX1234, ())
    assert source.reloads == 0
    assert look_up(source, "EX1234QFN", reread=True) == Found(newer, ())
    assert source.reloads == 1
    # A source with nothing to re-read is just asked.
    assert look_up(InMemoryChipSource([EX1234]), "EX1234QFN", reread=True) == Found(EX1234, ())


def test_look_up_refuses_an_answer_that_isnt_a_chip() -> None:
    class _Wrong(InMemoryChipSource):
        def get(self, mpn: str):  # type: ignore[override]
            return {"mpn": mpn, "customer": "Acme"}

    with pytest.raises(TypeError):
        look_up(_Wrong(), "EX1234QFN")


# --- stream safety: the tag ------------------------------------------------


def test_chip_panel_is_stream_safe() -> None:
    assert ChipPanel.sensitive is False


def test_the_tag_is_the_panels_own_decision_not_the_inherited_default() -> None:
    assert vars(ChipPanel)["sensitive"] is False


# --- stream safety: only Chip fields reach the card ------------------------


def test_every_chip_field_is_either_shown_or_left_out_on_purpose() -> None:
    """A field added to ``Chip`` later fails here until someone decides."""
    fields = {f.name for f in dataclasses.fields(Chip)}
    assert fields == SHOWN_FIELDS | LEFT_OUT_FIELDS
    assert not SHOWN_FIELDS & LEFT_OUT_FIELDS


def _tagged_chip() -> tuple[Chip, dict[str, str]]:
    """A chip whose every text value is a tag naming the field it sits in."""
    tags = {
        "mpn": "TAGMPN",
        "manufacturer": "TAGMAKER",
        "family": "TAGFAMILY",
        "package": "TAGPACKAGE",
        "package_family": "TAGPKGFAMILY",
        "functions": "TAGFUNCTION",
        "description": "TAGDESCRIPTION",
        "sources": "TAGCREDIT",
        "markings.code": "TAGCODE",
        "markings.examples": "TAGEXAMPLE",
        "markings.note": "TAGMARKNOTE",
        "documents.kind": "TAGDOCKIND",
        "documents.filename": "TAGDOCFILE",
        "documents.sha256": "TAGDOCHASH",
        "documents.url": "TAGDOCURL",
        "documents.note": "TAGDOCNOTE",
        "substitutes.mpn": "TAGSUBMPN",
        "substitutes.note": "TAGSUBNOTE",
        "used_in.board": "TAGBOARD",
        "used_in.designator": "TAGDESIGNATOR",
        "used_in.role": "TAGROLE",
    }
    chip = Chip(
        mpn=tags["mpn"],
        manufacturer=tags["manufacturer"],
        family=tags["family"],
        package=tags["package"],
        package_family=tags["package_family"],
        functions=(tags["functions"],),
        description=tags["description"],
        sources=(tags["sources"],),
        markings=(
            Marking(tags["markings.code"], (tags["markings.examples"],), tags["markings.note"]),
        ),
        documents=(
            Document(
                tags["documents.kind"],
                tags["documents.filename"],
                3,
                (tags["documents.sha256"],),
                tags["documents.url"],
                tags["documents.note"],
            ),
        ),
        substitutes=(Substitute(tags["substitutes.mpn"], tags["substitutes.note"]),),
        used_in=(UsedIn(tags["used_in.board"], tags["used_in.designator"], tags["used_in.role"]),),
    )
    return chip, tags


#: The tagged values the card is allowed to draw: the shown fields, and of a
#: marking only its code.
_ON_AIR = {"mpn", "manufacturer", "family", "package", "functions", "description", "markings.code"}


def test_the_tagged_chip_tags_every_text_value_there_is() -> None:
    """The proof below is only as good as its chip: no text value goes untagged."""
    chip, tags = _tagged_chip()

    def texts(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, tuple):
            return [text for item in value for text in texts(item)]
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return [t for f in dataclasses.fields(value) for t in texts(getattr(value, f.name))]
        return []

    assert sorted(texts(chip)) == sorted(tags.values())
    assert {name.split(".")[0] for name in tags} == {f.name for f in dataclasses.fields(Chip)}
    assert {name.split(".")[0] for name in _ON_AIR} == SHOWN_FIELDS


def test_every_field_inside_a_chip_is_accounted_for_too() -> None:
    """A text field added to a marking, a document, a substitute or a sighting
    fails here until it has a tag — and so a place in the proof below."""
    _chip, tags = _tagged_chip()
    not_text = {"documents.pages"}
    inside = {
        f"{name}.{f.name}"
        for name, kind in (
            ("markings", Marking),
            ("documents", Document),
            ("substitutes", Substitute),
            ("used_in", UsedIn),
        )
        for f in dataclasses.fields(kind)
    }
    assert inside == {name for name in tags if "." in name} | not_text


def _drawn_tags(qtbot, chip: Chip, tags: dict[str, str]) -> set[str]:
    panel = _panel(qtbot, InMemoryChipSource([chip]))
    panel.set_chip(chip.mpn)
    assert panel.state is State.SHOWN
    drawn = _everything_drawn(panel)
    return {name for name, tag in tags.items() if tag in drawn}


@pytest.mark.parametrize(
    "emptied",
    [
        {"manufacturer": ""},
        {"family": None},
        {"package": None},
        {"functions": ()},
        {"description": None},
        {"description": ""},
        {"markings": None},  # each marking keeps its examples and note, loses its code
        {"manufacturer": "", "family": None, "package": None, "functions": (), "description": None},
    ],
)
def test_a_left_out_value_never_stands_in_for_an_empty_shown_one(qtbot, emptied) -> None:
    """No fallbacks: an empty description isn't filled from a note, nor a code from an example."""
    chip, tags = _tagged_chip()
    changes = dict(emptied)
    if "markings" in changes:
        changes["markings"] = tuple(dataclasses.replace(m, code="") for m in chip.markings)
    sparse = dataclasses.replace(chip, **changes)
    gone = {"markings.code" if name == "markings" else name for name in emptied}
    assert _drawn_tags(qtbot, sparse, tags) == _ON_AIR - gone


def test_only_the_shown_fields_reach_the_card(qtbot) -> None:
    """Notes, documents, substitutes, sightings, examples and credits stay off."""
    chip, tags = _tagged_chip()
    panel = _panel(qtbot, InMemoryChipSource([chip]))
    panel.set_chip(chip.mpn)
    assert panel.state is State.SHOWN

    drawn = _everything_drawn(panel)
    on_card = {name for name, tag in tags.items() if tag in drawn}
    assert on_card == _ON_AIR


def test_source_names_and_losing_values_stay_off_the_card(qtbot) -> None:
    """A merged chip brings provenance with it; the card takes the chip only."""
    chip, _tags = _tagged_chip()
    merged = MergedChip(
        chip=chip,
        sources=("TAGSOURCEA", "TAGSOURCEB"),
        scalar_sources=(("package", "TAGSOURCEA"),),
        item_sources=(("markings", (("TAGSOURCEB",),)),),
        conflicts=(
            Conflict("package", (("TAGSOURCEA", "TAGPACKAGE"), ("TAGSOURCEB", "TAGLOSER"))),
            Conflict("TAGFIELD", (("TAGSOURCEA", "x"), ("TAGSOURCEB", "TAGLOSER2"))),
        ),
    )

    class _Merging(InMemoryChipSource):
        def merged(self, mpn: str) -> MergedChip:
            return merged

    panel = _panel(qtbot, _Merging([chip]))
    panel.set_chip(chip.mpn)
    drawn = _everything_drawn(panel)
    assert "Sources disagree on package." in drawn
    for off_air in ("TAGSOURCEA", "TAGSOURCEB", "TAGLOSER", "TAGLOSER2", "TAGFIELD"):
        assert off_air not in drawn


def test_the_part_number_asked_for_is_never_drawn(qtbot) -> None:
    """Whatever a caller hands ``set_chip``, the card shows only what a chip says.

    So a caller that passed the wrong string — a ticket ref, a customer's name —
    can't put it on air: a miss says "no chip", without repeating the question.
    """
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    for asked in ("Acme Corp / ticket T-4471", "R26-0004"):
        panel.set_chip(asked)
        assert panel.state is State.NOT_FOUND
        drawn = _everything_drawn(panel)
        assert "Acme" not in drawn and "T-4471" not in drawn and "R26-0004" not in drawn


def test_a_found_chip_is_drawn_in_the_references_spelling_not_the_callers(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    panel.set_chip("  ex1234qfn ")
    assert panel.title_label.text() == "EX1234QFN"
    assert "ex1234qfn" not in _everything_drawn(panel)


def test_the_panel_is_handed_a_chip_source_and_nothing_else() -> None:
    """No job, ticket or engagement provider — unlike the pinout crib's ref line."""
    params = list(inspect.signature(ChipPanel.__init__).parameters)
    assert params == ["self", "chip_source", "executor", "parent"]


def test_the_module_imports_nothing_that_holds_job_or_engagement_data() -> None:
    tree = ast.parse(Path(chip_module.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("benchhud")
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name.startswith("benchhud")
    }
    assert imported == {
        "benchhud.chips.model",
        "benchhud.chips.source",
        "benchhud.shell.panel",
        "benchhud.shell.qt_util",
    }


def test_the_panel_holds_only_widgets_the_proofs_can_read(qtbot) -> None:
    """``_everything_drawn`` reads labels, buttons and the box. A widget of
    another kind added later would draw where these tests can't see."""
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    panel.set_chip("EX1234QFN")
    known = (QLabel, QPushButton, QLineEdit)
    for child in panel.findChildren(QWidget):
        assert isinstance(child, known) or type(child) is QWidget, type(child).__name__


def test_the_box_holds_the_operators_typing_and_nothing_a_caller_passed(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    panel.set_chip("Acme Corp / ticket T-4471")
    assert panel.part_edit.text() == ""
    panel.part_edit.setText("EX0000")
    panel.part_edit.returnPressed.emit()
    assert panel.state is State.NOT_FOUND
    assert panel.part_edit.text() == "EX0000"  # their own typing, there to correct
    assert "EX0000" not in panel.message_label.text()


def test_chip_text_is_drawn_as_plain_text(qtbot) -> None:
    """A record can't put markup — or a local image — on the card."""
    markup = '<img src="file:///etc/hosts"><b>bold</b>'
    chip = Chip(
        mpn=markup,
        manufacturer=markup,
        family=markup,
        package=markup,
        functions=(markup,),
        markings=(Marking("<b>X</b>"),),
        description=markup,
    )
    panel = _panel(qtbot, InMemoryChipSource([chip]))
    panel.set_chip(markup)
    assert panel.state is State.SHOWN
    labels = panel.card.findChildren(QLabel)
    drawing_data = [label for label in labels if "<" in label.text()]
    assert len(drawing_data) == 6  # title, maker, package, functions, marks, description
    # A value label draws its text itself, as characters; nothing parses it.
    assert all(type(label) is ValueLabel for label in drawing_data)
    for label in [*drawing_data, panel.message_label, panel.disputed_label]:
        assert label.textFormat() == Qt.TextFormat.PlainText


def test_no_tooltip_carries_chip_text(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    before = {child: child.toolTip() for child in [panel, *panel.findChildren(QWidget)]}
    panel.set_chip("EX1234QFN")
    assert {child: child.toolTip() for child in before} == before


# --- empty states ----------------------------------------------------------


def test_no_chip_data_configured_is_said(qtbot) -> None:
    panel = _panel(qtbot, NullChipSource())
    assert panel.state is State.NO_DATA
    assert "No chip data configured" in panel.message_label.text()
    assert "Preferences" in panel.message_label.text()
    assert _shown(panel.message_label) and panel.card.isHidden()


def test_no_chip_chosen_is_said(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    assert panel.state is State.NO_CHIP
    assert panel.message_label.text() == "No chip chosen."
    assert _shown(panel.message_label) and panel.card.isHidden()
    assert panel.chip is None
    assert not panel.clear_button.isEnabled()


def test_no_chip_data_wins_over_a_chosen_chip_and_asks_nothing(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, NullChipSource(), held)
    panel.set_chip("EX1234QFN")
    assert panel.state is State.NO_DATA
    assert held.jobs == []  # nothing to look up in


def test_a_part_number_the_reference_doesnt_have_is_said(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    panel.set_chip("EX0000")
    assert panel.state is State.NOT_FOUND
    assert "No chip with that part number" in panel.message_label.text()
    assert panel.card.isHidden() and panel.chip is None


# --- showing a chip --------------------------------------------------------


def test_set_chip_shows_the_chip_from_the_fixture_index(qtbot) -> None:
    source = CompositeChipSource([DirChipSource.from_config(str(FIXTURE_DIR), "community")])
    panel = _panel(qtbot, source)
    panel.set_chip("EX1234QFN")
    assert panel.state is State.SHOWN
    assert panel.chip is not None and panel.chip.mpn == "EX1234QFN"
    assert panel.title_label.text() == "EX1234QFN"
    assert panel.maker_label.text() == "Example Semi · EX1234 family"
    assert panel.package_label.text() == "QFN-20 (3x3)"
    assert panel.functions_label.text() == "buck controller"
    assert panel.markings_label.text() == "X1="
    assert panel.description_label.text() == "Single-phase buck controller for a standby rail"
    assert not panel.card.isHidden() and panel.message_label.isHidden()
    assert panel.disputed_label.isHidden()
    assert panel.clear_button.isEnabled()


def test_a_row_with_nothing_to_say_is_hidden(qtbot) -> None:
    source = DirChipSource.from_config(str(FIXTURE_DIR), "community")
    panel = _panel(qtbot, source)
    panel.set_chip("SD77/NOPB")  # no package, function, marks or description
    assert panel.state is State.SHOWN
    assert _shown(panel.title_label) and _shown(panel.maker_label)
    for label in (
        panel.package_label,
        panel.functions_label,
        panel.markings_label,
        panel.description_label,
    ):
        assert label.isHidden()
    captions = [caption for caption, _value in panel._fact_rows]
    assert all(caption.isHidden() for caption in captions)

    panel.set_chip("EX1234QFN")  # and they come back for a chip that has them
    assert all(not caption.isHidden() for caption in captions)
    assert _shown(panel.package_label) and _shown(panel.description_label)


def test_sources_that_disagree_are_flagged(qtbot) -> None:
    other = dataclasses.replace(EX1234, package="QFN-24 (4x4)")
    source = CompositeChipSource(
        [InMemoryChipSource([other], name="overlay"), InMemoryChipSource([EX1234], name="base")]
    )
    panel = _panel(qtbot, source)
    panel.set_chip("EX1234QFN")
    assert panel.package_label.text() == "QFN-24 (4x4)"
    assert _shown(panel.disputed_label)
    assert panel.disputed_label.text() == "Sources disagree on package."
    drawn = _everything_drawn(panel)
    assert "overlay" not in drawn and "QFN-20" not in drawn


def test_clear_takes_the_chip_off(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    panel.part_edit.setText("EX1234QFN")
    panel.part_edit.returnPressed.emit()
    assert panel.state is State.SHOWN
    panel.clear_button.click()
    assert panel.state is State.NO_CHIP
    assert panel.chip is None and panel.card.isHidden()
    assert panel.part_edit.text() == ""
    assert not panel.part_edit.isUndoAvailable()  # the old text isn't one undo away
    assert "EX1234QFN" not in _everything_drawn(panel)


def test_a_blank_part_number_is_a_clear(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234]))
    panel.set_chip("EX1234QFN")
    panel.set_chip("   ")
    assert panel.state is State.NO_CHIP
    panel.set_chip("EX1234QFN")
    panel.set_chip(None)  # type: ignore[arg-type]
    assert panel.state is State.NO_CHIP


def test_typing_a_part_number_and_enter_shows_it(qtbot) -> None:
    panel = _panel(qtbot, InMemoryChipSource([EX1234, EX9000]))
    panel.part_edit.setText("ex9000sot")
    panel.part_edit.returnPressed.emit()
    assert panel.state is State.SHOWN
    assert panel.title_label.text() == "EX9000SOT"
    assert panel.part_edit.text() == "ex9000sot"  # the operator's typing stays put


def test_a_chip_set_from_elsewhere_empties_the_box(qtbot) -> None:
    """Else the box would name one chip while the card shows another."""
    panel = _panel(qtbot, InMemoryChipSource([EX1234, EX9000]))
    panel.part_edit.setText("ex9000sot")
    panel.part_edit.returnPressed.emit()
    panel.set_chip("EX1234QFN")
    assert panel.title_label.text() == "EX1234QFN"
    assert panel.part_edit.text() == ""


# --- the lookup is off the GUI thread, and a stale answer is dropped --------


def test_the_lookup_runs_off_the_calling_thread_by_default(qtbot) -> None:
    seen: list[threading.Thread] = []

    class _Watching(InMemoryChipSource):
        def get(self, mpn: str) -> Chip | None:
            seen.append(threading.current_thread())
            return super().get(mpn)

    source = _Watching([EX1234])
    panel = ChipPanel(lambda: source)
    qtbot.addWidget(panel)
    panel.set_chip("EX1234QFN")
    qtbot.waitUntil(lambda: panel.state is State.SHOWN)
    assert seen and all(thread is not threading.main_thread() for thread in seen)


def test_nothing_is_shown_while_a_lookup_is_out(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, InMemoryChipSource([EX1234, EX9000]), held)
    panel.set_chip("EX1234QFN")
    assert panel.state is State.LOOKING
    assert panel.message_label.text() == "Looking up…"
    assert panel.card.isHidden() and panel.chip is None
    held.jobs.pop()()
    assert panel.state is State.SHOWN

    # Choosing another chip takes the first one down at once, not when the
    # answer arrives.
    panel.set_chip("EX9000SOT")
    assert panel.state is State.LOOKING
    assert panel.card.isHidden() and panel.chip is None
    assert "EX1234QFN" not in _everything_drawn(panel)


def test_an_answer_for_a_chip_no_longer_chosen_is_dropped(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, InMemoryChipSource([EX1234, EX9000]), held)
    panel.set_chip("EX1234QFN")
    panel.set_chip("EX9000SOT")
    first, second = held.jobs

    second()
    assert panel.title_label.text() == "EX9000SOT"
    first()  # the slow answer for the chip the operator moved on from
    assert panel.state is State.SHOWN
    assert panel.title_label.text() == "EX9000SOT"
    assert panel.chip is not None and panel.chip.mpn == "EX9000SOT"


def test_a_stale_answer_that_lands_first_isnt_shown_either(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, InMemoryChipSource([EX1234, EX9000]), held)
    panel.set_chip("EX1234QFN")
    panel.set_chip("EX9000SOT")
    first, second = held.jobs

    first()
    assert panel.state is State.LOOKING and panel.card.isHidden()
    second()
    assert panel.title_label.text() == "EX9000SOT"


def test_an_answer_that_lands_after_a_clear_is_dropped(qtbot) -> None:
    held = _Held()
    panel = _panel(qtbot, InMemoryChipSource([EX1234]), held)
    panel.set_chip("EX1234QFN")
    panel.clear()
    held.jobs.pop()()
    assert panel.state is State.NO_CHIP
    assert panel.card.isHidden() and panel.chip is None


def test_an_answer_from_a_source_since_replaced_is_dropped(qtbot) -> None:
    """Preferences swapped the source mid-lookup: the old source's answer is stale."""
    held = _Held()
    current: list = [InMemoryChipSource([EX1234])]
    panel = ChipPanel(lambda: current[0], executor=held)
    qtbot.addWidget(panel)
    panel.set_chip("EX1234QFN")
    current[0] = InMemoryChipSource([dataclasses.replace(EX1234, package="QFN-24 (4x4)")])
    panel.refresh()
    first, second = held.jobs

    second()
    first()
    assert panel.package_label.text() == "QFN-24 (4x4)"


def test_an_answer_that_lands_after_the_sources_are_switched_off_is_dropped(qtbot) -> None:
    held = _Held()
    current: list = [InMemoryChipSource([EX1234])]
    panel = ChipPanel(lambda: current[0], executor=held)
    qtbot.addWidget(panel)
    panel.set_chip("EX1234QFN")
    current[0] = NullChipSource()
    panel.refresh()
    held.jobs.pop()()
    assert panel.state is State.NO_DATA
    assert panel.card.isHidden() and panel.chip is None


# --- nothing raises into the HUD -------------------------------------------


def test_a_worker_that_cant_start_is_said_not_raised(qtbot, caplog) -> None:
    def no_threads(job: Callable[[], None]) -> None:
        raise RuntimeError("can't start new thread")

    panel = _panel(qtbot, InMemoryChipSource([EX1234]), no_threads)
    panel.set_chip("EX1234QFN")
    assert panel.state is State.FAILED  # not left on "Looking up…"
    panel.refresh()
    assert panel.state is State.FAILED
    panel.part_edit.setText("EX1234QFN")
    panel.part_edit.returnPressed.emit()
    assert panel.state is State.FAILED
    assert "couldn't start a lookup" in caplog.text
    panel.clear()
    assert panel.state is State.NO_CHIP


def test_a_source_that_raises_is_said_not_raised(qtbot, caplog) -> None:
    class _Broken(InMemoryChipSource):
        def get(self, mpn: str) -> Chip | None:
            raise OSError("the checkout went away")

    panel = _panel(qtbot, _Broken())
    panel.set_chip("EX1234QFN")
    assert panel.state is State.FAILED
    assert "couldn't be read" in panel.message_label.text()
    assert panel.card.isHidden()
    assert "went away" not in _everything_drawn(panel)  # the error's own words stay in the log
    assert "chip panel's lookup failed" in caplog.text


def test_an_answer_that_isnt_a_chip_is_never_drawn(qtbot) -> None:
    class _Wrong(InMemoryChipSource):
        def get(self, mpn: str):  # type: ignore[override]
            return "customer: Acme"

    panel = _panel(qtbot, _Wrong())
    panel.set_chip("EX1234QFN")
    assert panel.state is State.FAILED
    assert "Acme" not in _everything_drawn(panel)


def test_a_failed_lookup_can_be_tried_again(qtbot) -> None:
    class _Flaky(InMemoryChipSource):
        fail = True

        def get(self, mpn: str) -> Chip | None:
            if self.fail:
                raise OSError("busy")
            return super().get(mpn)

    source = _Flaky([EX1234])
    panel = _panel(qtbot, source)
    panel.set_chip("EX1234QFN")
    assert panel.state is State.FAILED
    source.fail = False
    panel.refresh()
    assert panel.state is State.SHOWN


def test_a_lookup_that_outlives_the_panel_doesnt_raise(qtbot) -> None:
    held = _Held()
    panel = ChipPanel(lambda: InMemoryChipSource([EX1234]), executor=held)
    panel.set_chip("EX1234QFN")
    panel.deleteLater()
    qtbot.waitUntil(lambda: not _alive(panel))
    held.jobs.pop()()  # the worker finishes after the window is gone


def _alive(widget: QWidget) -> bool:
    try:
        widget.objectName()
    except RuntimeError:
        return False
    return True


# --- when the sources change ----------------------------------------------


def test_refresh_looks_the_chosen_chip_up_in_the_source_of_the_moment(qtbot) -> None:
    current: list = [InMemoryChipSource([EX1234])]
    panel = ChipPanel(lambda: current[0], executor=_inline)
    qtbot.addWidget(panel)
    panel.set_chip("EX1234QFN")
    assert panel.package_label.text() == "QFN-20 (3x3)"

    current[0] = InMemoryChipSource([dataclasses.replace(EX1234, package="QFN-24 (4x4)")])
    panel.refresh()
    assert panel.package_label.text() == "QFN-24 (4x4)"

    current[0] = InMemoryChipSource([])  # the source that had it was switched off
    panel.refresh()
    assert panel.state is State.NOT_FOUND and panel.card.isHidden()

    current[0] = NullChipSource()
    panel.refresh()
    assert panel.state is State.NO_DATA

    current[0] = InMemoryChipSource([EX1234])  # and the choice is remembered
    panel.refresh()
    assert panel.state is State.SHOWN


def test_refresh_has_the_source_read_again_before_it_answers(qtbot) -> None:
    """A source answers from the copy it holds while a re-read is running, so
    the panel waits for a read of its own — else the card comes back as it was."""
    newer = dataclasses.replace(EX1234, package="QFN-24 (4x4)")
    source = _Rereading([EX1234], on_disk=[newer])
    held = _Held()
    panel = _panel(qtbot, source, held)

    panel.set_chip("EX1234QFN")
    held.jobs.pop()()
    assert source.reloads == 0  # an ordinary pick reads nothing again
    assert panel.package_label.text() == "QFN-20 (3x3)"

    panel.refresh()
    assert panel.card.isHidden()  # down at once, before anything is read
    assert source.reloads == 0  # and the read is the worker's, not this thread's
    held.jobs.pop()()
    assert source.reloads == 1
    assert panel.package_label.text() == "QFN-24 (4x4)"


def test_refresh_drops_a_chip_the_source_no_longer_has(qtbot) -> None:
    source = _Rereading([EX1234], on_disk=[])
    panel = _panel(qtbot, source)
    panel.set_chip("EX1234QFN")
    assert panel.state is State.SHOWN
    panel.refresh()
    assert panel.state is State.NOT_FOUND and panel.card.isHidden()


def test_refresh_with_no_chip_chosen_reads_nothing(qtbot) -> None:
    source = _Rereading([EX1234])
    held = _Held()
    panel = _panel(qtbot, source, held)
    panel.refresh()
    assert held.jobs == [] and source.reloads == 0
    assert panel.state is State.NO_CHIP


def test_refresh_with_no_chip_chosen_tracks_whether_there_is_data(qtbot) -> None:
    current: list = [NullChipSource()]
    panel = ChipPanel(lambda: current[0], executor=_inline)
    qtbot.addWidget(panel)
    assert panel.state is State.NO_DATA
    current[0] = InMemoryChipSource([EX1234])
    panel.refresh()
    assert panel.state is State.NO_CHIP


# --- a narrow dock ---------------------------------------------------------


def test_a_long_record_never_forces_the_dock_wide(qtbot) -> None:
    chip = Chip(
        mpn="EX" + "9" * 80,
        manufacturer="Example Semi " * 10,
        package="QFN-" + "0" * 60,
        functions=("a-very-long-function-slug-" * 4,),
        markings=(Marking("X" * 40),),
        description="unbroken" * 60,
    )
    panel = _panel(qtbot, InMemoryChipSource([chip]))
    narrow, wanted = panel.minimumSizeHint().width(), panel.sizeHint().width()
    panel.set_chip(chip.mpn)
    assert panel.state is State.SHOWN
    assert panel.minimumSizeHint().width() == narrow
    assert panel.sizeHint().width() == wanted  # nor asks for more room than before
    assert narrow <= 260  # the dock width the panels are built for (#230)


def test_a_long_part_number_wraps_inside_the_word_instead_of_clipping(qtbot) -> None:
    """Clipped at the dock's edge, ``EX1234567890ABCDEFGQFN`` read as a shorter part."""
    panel = _panel(qtbot, InMemoryChipSource([Chip("EX1234567890ABCDEFGQFN", "Example Semi")]))
    panel.set_chip("EX1234567890ABCDEFGQFN")
    title = panel.title_label
    assert title.text() == "EX1234567890ABCDEFGQFN"  # whole, no ellipsis
    one_line = title.heightForWidth(2000)
    assert title.heightForWidth(120) >= 2 * one_line  # one word, and it still wraps
    # Laid out in a narrow panel, the label is given the height that takes.
    panel.resize(140, 400)
    panel.show()
    qtbot.waitExposed(panel)
    assert title.height() >= title.heightForWidth(title.width()) >= 2 * one_line


def _broken_into(label: ValueLabel, width: int) -> list[str]:
    layout, _height = label._lines(width)
    lines = [layout.lineAt(n) for n in range(layout.lineCount())]
    return [label.text()[line.textStart() : line.textStart() + line.textLength()] for line in lines]


def test_text_with_spaces_still_breaks_between_words(qtbot) -> None:
    """Inside a word only when the word itself is too long for the line."""
    label = ValueLabel()
    qtbot.addWidget(label)
    label.setText("standby rail")
    metrics = label.fontMetrics()
    # Room for "standby rai", not for the last letter.
    width = (
        metrics.horizontalAdvance("standby rai") + metrics.horizontalAdvance("standby rail")
    ) // 2
    assert [line.strip() for line in _broken_into(label, width)] == ["standby", "rail"]

    label.setText("EX1234567890ABCDEFGQFN")
    width = metrics.horizontalAdvance("EX1234567890") + 2
    pieces = _broken_into(label, width)
    assert len(pieces) == 2 and "".join(pieces) == "EX1234567890ABCDEFGQFN"


# --- the window ------------------------------------------------------------


def test_window_builds_the_chip_panel(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window.chip_panel, ChipPanel)
    assert window.chip_panel.sensitive is False
    assert window.panels.panel("chip") is window.chip_panel
    assert window.chip_panel.state is State.NO_DATA  # nothing configured


def test_the_chip_panel_sits_with_the_pinout_crib() -> None:
    group = group_of("chip")
    assert group is not None and group is group_of("pinout")


def _window_with_chips(qtbot) -> MainWindow:
    config = Config()
    config.chips.sources = [ChipSourceConfig(name="community", path=str(FIXTURE_DIR))]
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    return window


def test_the_window_hands_the_panel_its_chip_source(qtbot) -> None:
    window = _window_with_chips(qtbot)
    panel = window.chip_panel
    assert panel.state is State.NO_CHIP
    panel.set_chip("EX1234QFN")
    qtbot.waitUntil(lambda: panel.state is State.SHOWN)
    assert panel.title_label.text() == "EX1234QFN"


def test_a_preferences_change_reaches_the_panel(qtbot) -> None:
    config = Config()
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    panel = window.chip_panel
    assert panel.state is State.NO_DATA

    config.chips.sources = [ChipSourceConfig(name="community", path=str(FIXTURE_DIR))]
    window.reconfigure_chip_source()
    assert panel.state is State.NO_CHIP
    panel.set_chip("EX1234QFN")
    qtbot.waitUntil(lambda: panel.state is State.SHOWN)

    # Switching the only source off takes its chip off the panel.
    config.chips.sources = []
    window.reconfigure_chip_source()
    assert panel.state is State.NO_DATA
    assert panel.card.isHidden() and "EX1234QFN" not in _everything_drawn(panel)


def test_preferences_ok_with_the_same_sources_picks_up_an_edit_on_disk(qtbot, tmp_path) -> None:
    """The list is unchanged, so the window keeps the source and re-reads it on a
    worker. The card has to come back with what's on disk now — not with the
    copy the source was still holding when the panel asked."""
    body = 'schema = 1\nmpn = "EX1234QFN"\nmanufacturer = "Example Semi"\n'
    chip_file = tmp_path / "chips" / "example-semi" / "EX1234QFN.toml"
    chip_file.parent.mkdir(parents=True)
    chip_file.write_text(body + 'description = "The first wording"\n', encoding="utf-8")
    config = Config()
    config.chips.sources = [ChipSourceConfig(name="overlay", path=str(tmp_path))]
    window = MainWindow(config, job_store=JobStore(":memory:"))
    qtbot.addWidget(window)
    panel = window.chip_panel
    source = window.chip_source
    panel.set_chip("EX1234QFN")
    qtbot.waitUntil(lambda: panel.state is State.SHOWN)
    assert panel.description_label.text() == "The first wording"

    chip_file.write_text(body + 'description = "Reworded"\n', encoding="utf-8")
    window.reconfigure_chip_source()
    assert window.chip_source is source  # same list, same source
    assert panel.card.isHidden()  # the old wording is down at once
    qtbot.waitUntil(lambda: panel.state is State.SHOWN)
    assert panel.description_label.text() == "Reworded"

    chip_file.unlink()  # and a chip that's gone from disk goes from the card
    window.reconfigure_chip_source()
    qtbot.waitUntil(lambda: panel.state is not State.LOOKING)
    assert panel.state is State.NOT_FOUND and panel.card.isHidden()


def test_no_job_ticket_or_engagement_value_reaches_the_panel(qtbot) -> None:
    """The window knows a customer, a ticket and an engagement; the panel never does."""
    window = _window_with_chips(qtbot)
    window._ticket_source = InMemoryTicketSource(
        tickets=[Ticket(ref="T-4471", device="Laptop TAGDEVICE", customer="TAGCUSTOMER")]
    )
    window._recon_source = InMemoryReconSource(
        engagements=[Engagement("R26-0004", title="TAGENGAGEMENT", target_class="iot-lock")]
    )
    job = window._job_store.create_job(
        "TAGDEVICE board",
        serial="TAGSERIAL",
        customer="TAGCUSTOMER",
        symptoms="TAGSYMPTOM",
        external_ref="T-4471",
    )
    window.jobs_panel.refresh_jobs()
    window.jobs_panel.select_job(job.id)
    window.import_engagement("R26-0004")
    assert window._active_engagement == "R26-0004"
    window.pinout_panel.refresh()
    assert "R26-0004" in _everything_drawn(window.pinout_panel)  # the crib does show the ref

    panel = window.chip_panel
    for asked in ("EX1234QFN", "EX0000", ""):
        panel.set_chip(asked)
        qtbot.waitUntil(lambda: panel.state is not State.LOOKING)
        drawn = _everything_drawn(panel)
        for off_air in (
            "TAGCUSTOMER",
            "TAGDEVICE",
            "TAGSERIAL",
            "TAGSYMPTOM",
            "TAGENGAGEMENT",
            "T-4471",
            "R26-0004",
        ):
            assert off_air not in drawn
