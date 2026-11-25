"""Tests for merging chips from several sources, and the composite source.

Pure Python, no Qt, no network. Every chip here is made up — "Example Semi"
doesn't exist and neither do its parts — and the sources are named the way a
bench would have them: ``local`` (a private overlay) ahead of ``community``.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from benchhud.chips import (
    Chip,
    ChipSource,
    CompositeChipSource,
    Conflict,
    DirChipSource,
    Document,
    InMemoryChipSource,
    Marking,
    MarkingMatch,
    MatchKind,
    MergedChip,
    NullChipSource,
    Substitute,
    UsedIn,
    match_markings,
    merge_chips,
)
from benchhud.chips.composite import same_text

FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

_H1 = "a1" * 32
_H2 = "b2" * 32
_H3 = "c3" * 32


def _chip(mpn: str = "EX1234QFN", **fields) -> Chip:
    fields.setdefault("manufacturer", "Example Semi")
    return Chip(mpn=mpn, **fields)


def _merge(*chips: Chip, names=("local", "community", "third")) -> MergedChip:
    return merge_chips(list(zip(names, chips, strict=False)))


def _composite(*lists: list[Chip], names=("local", "community", "third")) -> CompositeChipSource:
    return CompositeChipSource(
        [InMemoryChipSource(chips, name) for name, chips in zip(names, lists, strict=False)]
    )


_FULL = _chip(
    family="EX1234",
    package="QFN-20 (3x3)",
    package_family="QFN",
    functions=("buck-controller",),
    description="Single-phase buck controller for a standby rail",
    sources=("bench-notes",),
    markings=(Marking(code="X1=", examples=("X1=6A",), note="Top line."),),
    documents=(Document(kind="datasheet", filename="EX1234.pdf", pages=14, sha256=(_H1,)),),
    substitutes=(Substitute(mpn="EX1235QFN", note="Check the enable threshold"),),
    used_in=(UsedIn(board="EXAMPLE-BOARD-1", designator="U7000", role="standby 3.3 V"),),
)


# --- one source -----------------------------------------------------------


def test_one_source_merges_to_the_chip_it_gave() -> None:
    merged = _merge(_FULL)
    assert merged.chip == _FULL
    assert type(merged.chip) is Chip
    assert merged.sources == ("local",)
    assert merged.conflicts == ()
    for name in ("mpn", "manufacturer", "family", "package", "package_family", "description"):
        assert merged.source_of(name) == "local"
    for name in ("functions", "sources", "markings", "documents", "substitutes", "used_in"):
        assert merged.sources_of(name) == (("local",),)


def test_a_value_the_chip_does_not_have_has_no_source() -> None:
    merged = _merge(_chip())
    assert merged.source_of("package") is None
    assert merged.source_of("description") is None
    assert merged.sources_of("markings") == ()
    assert merged.conflict("package") is None


def test_the_same_chip_from_two_sources_is_that_chip() -> None:
    merged = _merge(_FULL, _FULL)
    assert merged.chip == _FULL
    assert merged.sources == ("local", "community")
    assert merged.conflicts == ()
    assert merged.source_of("package") == "local"
    assert merged.sources_of("markings") == (("local", "community"),)


def test_nothing_to_merge_is_an_error() -> None:
    with pytest.raises(ValueError, match="no chips"):
        merge_chips([])


def test_a_merged_chip_is_immutable_and_hashable() -> None:
    merged = _merge(_FULL, _chip(package="SOP-8"))
    with pytest.raises(FrozenInstanceError):
        merged.chip = _FULL  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        merged.conflicts[0].field = "x"  # type: ignore[misc]
    assert len({merged, _merge(_FULL, _chip(package="SOP-8"))}) == 1
    assert hash(merged.chip) == hash(replace(merged.chip))


# --- single values --------------------------------------------------------


def test_the_first_source_with_a_value_wins_it() -> None:
    local = _chip(package="QFN-20 (3x3)")
    community = _chip(family="EX1234", package="QFN-20 (3x3)", description="A buck controller")
    merged = _merge(local, community)
    assert merged.chip.package == "QFN-20 (3x3)"
    assert merged.chip.family == "EX1234"
    assert merged.chip.description == "A buck controller"
    assert merged.source_of("package") == "local"
    assert merged.source_of("family") == "community"
    assert merged.source_of("description") == "community"
    assert merged.conflicts == ()


def test_order_is_precedence() -> None:
    a, b = _chip(description="From A"), _chip(description="From B")
    assert _merge(a, b).chip.description == "From A"
    assert _merge(b, a).chip.description == "From B"


def test_a_later_source_that_disagrees_is_kept_as_a_conflict() -> None:
    merged = _merge(_chip(package="QFN-20 (3x3)"), _chip(package="QFN-24 (4x4)"))
    assert merged.chip.package == "QFN-20 (3x3)"
    assert merged.conflicts == (
        Conflict("package", (("local", "QFN-20 (3x3)"), ("community", "QFN-24 (4x4)"))),
    )
    assert merged.conflict("package") is merged.conflicts[0]
    assert merged.conflict("description") is None


def test_a_conflict_holds_every_source_that_has_a_value_in_order() -> None:
    merged = _merge(
        _chip(description="Buck controller"),
        _chip(),  # says nothing
        _chip(description="LDO"),
        names=("local", "community", "third"),
    )
    assert merged.conflict("description").values == (
        ("local", "Buck controller"),
        ("third", "LDO"),
    )
    agreeing = _merge(
        _chip(description="Buck"), _chip(description="LDO"), _chip(description="Buck")
    )
    assert [source for source, _ in agreeing.conflict("description").values] == [
        "local",
        "community",
        "third",
    ]


@pytest.mark.parametrize("name", ["manufacturer", "family", "package", "description"])
def test_every_single_value_can_conflict(name: str) -> None:
    merged = _merge(_chip(**{name: "One thing"}), _chip(**{name: "Another thing"}))
    assert getattr(merged.chip, name) == "One thing"
    assert [c.field for c in merged.conflicts] == [name]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("QFN-20 (3x3)", "qfn-20 (3x3)"),
        ("QFN-20 (3x3)", "  QFN-20   (3x3) "),
        ("Example Semi", "EXAMPLE SEMI"),
        ("Buck controller", "buck\tcontroller"),
    ],
)
def test_case_and_stray_whitespace_are_not_a_disagreement(first: str, second: str) -> None:
    assert same_text(first, second)
    merged = _merge(_chip(description=first), _chip(description=second))
    assert merged.chip.description == first  # the first source's spelling
    assert merged.conflicts == ()


@pytest.mark.parametrize(
    ("first", "second"),
    [("QFN-20", "QFN-24"), ("QFN-20 (3x3)", "QFN-20(3x3)"), ("Buck", "Buck controller")],
)
def test_a_real_difference_is_a_disagreement(first: str, second: str) -> None:
    assert not same_text(first, second)
    assert _merge(_chip(description=first), _chip(description=second)).conflicts != ()


def test_a_blank_value_is_no_value() -> None:
    merged = _merge(_chip(description="   "), _chip(description="Buck"))
    assert merged.chip.description == "Buck"
    assert merged.source_of("description") == "community"
    assert merged.conflicts == ()


def test_two_spellings_of_a_part_number_are_one_chip_not_a_conflict() -> None:
    merged = _merge(_chip("EX1234QFN"), _chip("ex1234qfn"))
    assert merged.chip.mpn == "EX1234QFN"
    assert merged.source_of("mpn") == "local"
    assert merged.conflicts == ()


# --- the package family goes with the package -----------------------------


def test_the_family_is_not_borrowed_from_a_different_package() -> None:
    local = _chip(package="SOT-23-5")  # an overlay with no vocab: no family
    community = _chip(package="QFN-20 (3x3)", package_family="QFN")
    merged = _merge(local, community)
    assert merged.chip.package == "SOT-23-5"
    assert merged.chip.package_family is None
    assert merged.source_of("package_family") is None
    assert [c.field for c in merged.conflicts] == ["package"]


def test_the_family_comes_from_a_source_with_the_winning_package() -> None:
    local = _chip(package="QFN-20 (3x3)")
    community = _chip(package="qfn-20 (3x3)", package_family="QFN")
    merged = _merge(local, community)
    assert merged.chip.package == "QFN-20 (3x3)"
    assert merged.chip.package_family == "QFN"
    assert merged.source_of("package") == "local"
    assert merged.source_of("package_family") == "community"
    assert merged.conflicts == ()


def test_two_families_for_one_package_is_a_conflict() -> None:
    merged = _merge(
        _chip(package="SOT-23-5", package_family="SOT-23"),
        _chip(package="SOT-23-5", package_family="SOT"),
    )
    assert merged.chip.package_family == "SOT-23"
    assert merged.conflict("package_family").values == (("local", "SOT-23"), ("community", "SOT"))


def test_no_package_no_family() -> None:
    merged = _merge(_chip(), _chip(package="QFN-20", package_family="QFN"))
    assert (merged.chip.package, merged.chip.package_family) == ("QFN-20", "QFN")
    assert merged.source_of("package_family") == "community"


# --- lists ----------------------------------------------------------------


def test_lists_are_joined_with_the_first_source_first() -> None:
    merged = _merge(
        _chip(functions=("ldo", "load-switch"), sources=("bench-notes",)),
        _chip(functions=("buck-controller", "LDO"), sources=("someone-else", "bench-notes")),
    )
    assert merged.chip.functions == ("ldo", "load-switch", "buck-controller")
    assert merged.sources_of("functions") == (("local", "community"), ("local",), ("community",))
    assert merged.chip.sources == ("bench-notes", "someone-else")
    assert merged.sources_of("sources") == (("local", "community"), ("community",))


def test_the_same_marking_code_is_one_marking_with_the_examples_joined() -> None:
    merged = _merge(
        _chip(markings=(Marking(code="X1=", examples=("X1=6A",)),)),
        _chip(markings=(Marking(code=" X1= ", examples=("X1=7C", "X1=6A"), note="Top line."),)),
    )
    assert merged.chip.markings == (
        Marking(code="X1=", examples=("X1=6A", "X1=7C"), note="Top line."),
    )
    assert merged.sources_of("markings") == (("local", "community"),)


def test_marking_codes_keep_their_case() -> None:
    merged = _merge(_chip(markings=(Marking(code="aB"),)), _chip(markings=(Marking(code="AB"),)))
    assert [m.code for m in merged.chip.markings] == ["aB", "AB"]
    assert merged.sources_of("markings") == (("local",), ("community",))


def test_the_first_note_on_a_marking_stands() -> None:
    merged = _merge(
        _chip(markings=(Marking(code="X1=", note="Mine."),)),
        _chip(markings=(Marking(code="X1=", note="Theirs."),)),
    )
    assert merged.chip.markings[0].note == "Mine."
    assert merged.conflicts == ()  # notes inside an entry don't raise a flag


def _doc(filename: str = "EX1234.pdf", kind: str = "datasheet", **fields) -> Document:
    return Document(kind=kind, filename=filename, **fields)


def test_documents_that_share_a_hash_are_one_document() -> None:
    merged = _merge(
        _chip(documents=(_doc("EX1234.pdf", sha256=(_H1,)),)),
        _chip(
            documents=(_doc("ex1234-datasheet-rev-c.pdf", "app-note", sha256=(_H2, _H1.upper())),)
        ),
    )
    (document,) = merged.chip.documents
    assert (document.kind, document.filename) == ("datasheet", "EX1234.pdf")
    assert document.sha256 == (_H1, _H2)
    assert merged.sources_of("documents") == (("local", "community"),)


def test_documents_with_the_same_kind_and_name_are_one_document() -> None:
    # The same revision saved by two sites hashes differently.
    merged = _merge(
        _chip(documents=(_doc(sha256=(_H1,)),)),
        _chip(
            documents=(_doc("ex1234.PDF", pages=14, sha256=(_H2,), url="https://example.com/x"),)
        ),
        _chip(documents=(_doc(note="No public source known"),)),
    )
    assert merged.chip.documents == (
        _doc(
            pages=14,
            sha256=(_H1, _H2),
            url="https://example.com/x",
            note="No public source known",
        ),
    )
    assert merged.sources_of("documents") == (("local", "community", "third"),)


def test_documents_that_share_neither_stay_apart() -> None:
    merged = _merge(
        _chip(documents=(_doc("EX1234.pdf", sha256=(_H1,)),)),
        _chip(
            documents=(
                _doc("EX1234.pdf", "app-note", sha256=(_H2,)),  # same name, another kind
                _doc("EX1234-layout.pdf", sha256=(_H3,)),  # same kind, another name
            )
        ),
    )
    assert [(d.kind, d.filename) for d in merged.chip.documents] == [
        ("datasheet", "EX1234.pdf"),
        ("app-note", "EX1234.pdf"),
        ("datasheet", "EX1234-layout.pdf"),
    ]
    assert merged.sources_of("documents") == (("local",), ("community",), ("community",))


def test_which_documents_end_up_together_does_not_depend_on_the_order() -> None:
    # One source's document bridges two of another's.
    two = _chip(documents=(_doc("x.pdf", sha256=(_H1,)), _doc("y.pdf", sha256=(_H2,))))
    bridge = _chip(documents=(_doc("z.pdf", sha256=(_H1, _H2)),))
    for first, second in ((two, bridge), (bridge, two)):
        merged = _merge(first, second)
        (document,) = merged.chip.documents
        assert set(document.sha256) == {_H1, _H2}
        assert merged.sources_of("documents") == (("local", "community"),)
    assert _merge(two, bridge).chip.documents[0].filename == "x.pdf"
    assert _merge(bridge, two).chip.documents[0].filename == "z.pdf"


def test_a_name_that_was_merged_away_still_matches() -> None:
    # a.pdf and b.pdf share a hash; a hashless b.pdf is the same file by name.
    chips = [
        _chip(documents=(_doc("a.pdf", sha256=(_H1,)),)),
        _chip(documents=(_doc("b.pdf", sha256=(_H1,)),)),
        _chip(documents=(_doc("b.pdf", pages=9),)),
    ]
    for order in itertools.permutations(chips):
        merged = _merge(*order)
        (document,) = merged.chip.documents
        assert document.sha256 == (_H1,)
        assert document.pages == 9
        assert merged.sources_of("documents") == (("local", "community", "third"),)


def test_a_hash_is_kept_once_however_it_is_written() -> None:
    merged = _merge(
        _chip(documents=(_doc(sha256=(_H1,)),)),
        _chip(documents=(_doc(sha256=(_H1.upper(), f" {_H1} ", "", _H2.upper(), _H2)),)),
    )
    assert merged.chip.documents[0].sha256 == (_H1, _H2.upper())


def test_a_page_count_of_nought_is_still_a_page_count() -> None:
    merged = _merge(_chip(documents=(_doc(pages=0),)), _chip(documents=(_doc(pages=20),)))
    assert merged.chip.documents[0].pages == 0


def test_the_first_page_count_stands() -> None:
    merged = _merge(_chip(documents=(_doc(pages=14),)), _chip(documents=(_doc(pages=20),)))
    assert merged.chip.documents[0].pages == 14


def test_substitutes_are_one_per_part_number() -> None:
    merged = _merge(
        _chip(substitutes=(Substitute(mpn="EX1235QFN"),)),
        _chip(
            substitutes=(
                Substitute(mpn="ex1235qfn", note="Check the enable threshold"),
                Substitute(mpn="EX1236QFN"),
            )
        ),
    )
    assert merged.chip.substitutes == (
        Substitute(mpn="EX1235QFN", note="Check the enable threshold"),
        Substitute(mpn="EX1236QFN"),
    )
    assert merged.sources_of("substitutes") == (("local", "community"), ("community",))


def test_board_sightings_are_one_per_board_and_designator() -> None:
    merged = _merge(
        _chip(
            used_in=(
                UsedIn(board="EXAMPLE-BOARD-1", designator="U7000"),
                UsedIn(board="EXAMPLE-BOARD-1"),
            )
        ),
        _chip(
            used_in=(
                UsedIn(board="example-board-1", designator="u7000", role="standby 3.3 V"),
                UsedIn(board="EXAMPLE-BOARD-1", designator="U7100"),
                UsedIn(board="EXAMPLE-BOARD-2", designator="U7000"),
            )
        ),
    )
    assert merged.chip.used_in == (
        UsedIn(board="EXAMPLE-BOARD-1", designator="U7000", role="standby 3.3 V"),
        UsedIn(board="EXAMPLE-BOARD-1"),  # no designator: its own entry
        UsedIn(board="EXAMPLE-BOARD-1", designator="U7100"),
        UsedIn(board="EXAMPLE-BOARD-2", designator="U7000"),
    )
    assert merged.sources_of("used_in") == (
        ("local", "community"),
        ("local",),
        ("community",),
        ("community",),
    )


def test_sightings_with_no_designator_are_one_per_board() -> None:
    merged = _merge(
        _chip(used_in=(UsedIn(board="EXAMPLE-BOARD-1", role="standby 3.3 V"),)),
        _chip(used_in=(UsedIn(board="example-board-1", role="something else"),)),
    )
    assert merged.chip.used_in == (UsedIn(board="EXAMPLE-BOARD-1", role="standby 3.3 V"),)


def test_credits_are_compared_without_case() -> None:
    merged = _merge(_chip(sources=("bench-notes",)), _chip(sources=("Bench-Notes", "other")))
    assert merged.chip.sources == ("bench-notes", "other")


def test_a_later_source_can_disagree_after_one_that_agrees() -> None:
    merged = _merge(_chip(package="QFN-20"), _chip(package="qfn-20"), _chip(package="QFN-24"))
    assert merged.conflict("package").values == (
        ("local", "QFN-20"),
        ("community", "qfn-20"),
        ("third", "QFN-24"),
    )


def test_a_source_named_twice_is_named_once() -> None:
    merged = _merge(_chip(description="A"), _chip(description="B"), names=("same", "same"))
    assert merged.sources == ("same",)


def test_a_chip_with_no_part_number_or_maker_anywhere_keeps_the_blanks() -> None:
    merged = _merge(Chip(mpn="", manufacturer=""), Chip(mpn=" ", manufacturer=" "))
    assert (merged.chip.mpn, merged.chip.manufacturer) == ("", "")
    assert merged.source_of("mpn") is None


def test_an_entry_a_source_lists_twice_names_that_source_once() -> None:
    merged = _merge(_chip(functions=("ldo", "LDO"), markings=(Marking("X1="), Marking("X1="))))
    assert merged.chip.functions == ("ldo",)
    assert merged.chip.markings == (Marking("X1="),)
    assert merged.sources_of("functions") == (("local",),)
    assert merged.sources_of("markings") == (("local",),)


def test_sources_line_up_with_the_lists_they_describe() -> None:
    merged = _merge(_FULL, _chip(functions=("ldo",), markings=(Marking("X9"),)))
    for name in ("functions", "sources", "markings", "documents", "substitutes", "used_in"):
        assert len(merged.sources_of(name)) == len(getattr(merged.chip, name))
    assert dict(zip(merged.chip.markings, merged.sources_of("markings"), strict=True)) == {
        _FULL.markings[0]: ("local",),
        Marking("X9"): ("community",),
    }


# --- the composite source: a part number -----------------------------------


def test_it_is_a_chip_source() -> None:
    assert isinstance(CompositeChipSource(), ChipSource)
    assert CompositeChipSource().name == "chips"
    assert CompositeChipSource(name="all").name == "all"


def test_no_sources_finds_nothing() -> None:
    for source in (CompositeChipSource(), CompositeChipSource([NullChipSource()])):
        assert source.get("EX1234QFN") is None
        assert source.merged("EX1234QFN") is None
        assert source.by_marking("X1=") == []


def test_get_is_the_merged_chip_and_a_plain_one() -> None:
    source = _composite(
        [_chip(package="QFN-20 (3x3)")],
        [_chip("ex1234qfn", description="Buck", markings=(Marking("X1="),))],
    )
    chip = source.get(" ex1234QFN ")
    assert type(chip) is Chip
    assert chip == _chip(package="QFN-20 (3x3)", description="Buck", markings=(Marking("X1="),))
    assert chip == source.merged("EX1234QFN").chip


def test_merged_says_where_each_part_came_from() -> None:
    source = _composite(
        [_chip(package="QFN-20 (3x3)", markings=(Marking("X1="),))],
        [_chip(package="QFN-24 (4x4)", description="Buck", markings=(Marking("X1="),))],
    )
    merged = source.merged("EX1234QFN")
    assert merged.sources == ("local", "community")
    assert merged.source_of("package") == "local"
    assert merged.source_of("description") == "community"
    assert merged.sources_of("markings") == (("local", "community"),)
    assert merged.conflict("package").values == (
        ("local", "QFN-20 (3x3)"),
        ("community", "QFN-24 (4x4)"),
    )


def test_a_chip_only_a_later_source_has_is_still_found() -> None:
    source = _composite([_chip("EX9000SOT")], [_chip("EX1234QFN", description="Buck")])
    assert source.get("EX1234QFN").description == "Buck"
    assert source.merged("EX1234QFN").sources == ("community",)
    assert source.get("EX0000") is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_nothing_typed_is_no_chip(blank: str) -> None:
    source = _composite([_chip("")], [_FULL])
    assert source.get(blank) is None
    assert source.merged(blank) is None


def test_the_order_of_the_sources_is_what_wins() -> None:
    mine, theirs = [_chip(description="Mine")], [_chip(description="Theirs")]
    assert _composite(mine, theirs).get("EX1234QFN").description == "Mine"
    assert _composite(theirs, mine).get("EX1234QFN").description == "Theirs"


# --- the composite source: a marking ---------------------------------------


def test_a_chip_in_two_sources_is_one_candidate_shown_merged() -> None:
    source = _composite(
        [_chip(package="QFN-20 (3x3)", markings=(Marking("X1="),))],
        [_chip(description="Buck", functions=("buck-controller",), markings=(Marking("X1="),))],
    )
    (match,) = source.by_marking("X1=6A")
    assert isinstance(match, MarkingMatch)
    assert type(match.chip) is Chip
    assert match.chip == source.get("EX1234QFN")
    assert match.chip.package == "QFN-20 (3x3)"
    assert match.chip.functions == ("buck-controller",)


def test_candidates_from_every_source_are_ranked_together() -> None:
    source = _composite(
        [
            _chip("EX2000QFN", markings=(Marking("X1"),)),
            _chip("EX4000QFN", markings=(Marking("X1=6"),)),
        ],
        [
            _chip("EX1000QFN", markings=(Marking("X1="),)),
            _chip("EX3000QFN", markings=(Marking("x1="),)),
        ],
    )
    found = source.by_marking("X1=")
    assert [(m.chip.mpn, m.kind) for m in found] == [
        ("EX1000QFN", MatchKind.EXACT),
        ("EX3000QFN", MatchKind.EXACT),  # case folded: just below
        ("EX2000QFN", MatchKind.WHOLE_MARK),
        ("EX4000QFN", MatchKind.PARTIAL),
    ]
    everything = [c for s in source.sources for c in s.chips]
    assert found == match_markings(everything, "X1=")


def test_a_marking_only_a_later_source_knows_still_shows_the_merged_chip() -> None:
    source = _composite(
        [_chip(package="QFN-20 (3x3)")],  # the overlay: a package, no marking
        [_chip(package="QFN-24 (4x4)", markings=(Marking("X1="),))],
    )
    (match,) = source.by_marking("X1=6A")
    assert match.chip.package == "QFN-20 (3x3)"  # the overlay's, though it didn't match
    assert match.marking == Marking("X1=")


def test_a_candidate_ranks_under_the_best_marking_any_source_has() -> None:
    source = _composite(
        [_chip(markings=(Marking("X1"),))],
        [_chip(markings=(Marking("X1="),))],
    )
    (match,) = source.by_marking("X1=6A")
    assert match.marking == Marking("X1=")
    assert match.kind is MatchKind.WHOLE_MARK
    assert match.score == match_markings([source.get("EX1234QFN")], "X1=6A")[0].score


def test_the_same_marking_on_different_chips_gives_every_chip() -> None:
    source = _composite(
        [_chip("EX1234QFN", package="QFN-20 (3x3)", markings=(Marking("X1="),))],
        [_chip("EX9000SOT", package="SOT-23-5", markings=(Marking("X1="),))],
    )
    assert [(m.chip.mpn, m.chip.package) for m in source.by_marking("X1=6A")] == [
        ("EX1234QFN", "QFN-20 (3x3)"),
        ("EX9000SOT", "SOT-23-5"),
    ]


def test_nothing_typed_matches_nothing() -> None:
    assert _composite([_FULL], [_FULL]).by_marking("") == []
    assert _composite([_FULL], [_FULL]).by_marking("   ") == []


# --- a source that misbehaves ---------------------------------------------


class _Broken:
    """A source that breaks its promise not to raise."""

    name = "broken"

    def get(self, mpn: str) -> Chip | None:
        raise RuntimeError("get fell over")

    def by_marking(self, text: str) -> list[MarkingMatch]:
        raise RuntimeError("by_marking fell over")


class _Wrong:
    """A source that answers a different question than the one asked."""

    name = "wrong"

    def __init__(self, answer: object) -> None:
        self.answer = answer

    def get(self, mpn: str) -> object:
        return self.answer

    def by_marking(self, text: str) -> list:
        return []


class _OwnMatcher:
    """A source that offers a chip by a rule of its own (a fuzzy matcher, say)."""

    name = "fuzzy"

    def __init__(self, chip: Chip) -> None:
        self.chip = chip

    def get(self, mpn: str) -> Chip | None:
        return self.chip if self.chip.key == mpn.strip().casefold() else None

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return [MarkingMatch(self.chip, self.chip.markings[0], MatchKind.PARTIAL, False, 7)]


def test_one_source_failing_does_not_take_the_others_down(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")
    good = InMemoryChipSource([_FULL], "community")
    for sources in ([_Broken(), good], [good, _Broken()]):
        source = CompositeChipSource(sources)
        assert source.get("EX1234QFN") == _FULL
        assert source.merged("EX1234QFN").sources == ("community",)
        assert [m.chip for m in source.by_marking("X1=6A")] == [_FULL]
    assert all("broken" in record.getMessage() for record in caplog.records)
    assert len(caplog.records) >= 4


def test_every_source_failing_is_just_nothing(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")
    source = CompositeChipSource([_Broken(), _Broken()])
    assert source.get("EX1234QFN") is None
    assert source.merged("EX1234QFN") is None
    assert source.by_marking("X1=") == []


@pytest.mark.parametrize("answer", [_chip("EX9000SOT"), "EX1234QFN", {"mpn": "EX1234QFN"}, 7])
def test_an_answer_that_is_not_the_chip_asked_for_is_ignored(answer: object) -> None:
    source = CompositeChipSource([_Wrong(answer), InMemoryChipSource([_FULL], "community")])
    assert source.get("EX1234QFN") == _FULL
    assert source.merged("EX1234QFN").sources == ("community",)
    assert CompositeChipSource([_Wrong(answer)]).get("EX1234QFN") is None


def test_a_chip_shaped_thing_that_is_not_a_chip_does_not_raise(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")
    mangled = replace(_FULL, functions=None)  # type: ignore[arg-type]
    source = CompositeChipSource([InMemoryChipSource([mangled], "local")])
    assert source.get("EX1234QFN") is None
    assert source.by_marking("X1=6A") == []
    assert caplog.records


def test_a_source_with_a_matcher_of_its_own_keeps_its_candidate() -> None:
    odd = _chip("EX7000QFN", markings=(Marking("ZZ"),))
    source = CompositeChipSource(
        [InMemoryChipSource([_FULL, replace(odd, package="QFN-16")], "local"), _OwnMatcher(odd)]
    )
    found = source.by_marking("X1=6A")
    assert [m.chip.mpn for m in found] == ["EX1234QFN", "EX7000QFN"]
    assert found[1].score == 7
    assert found[1].chip.package == "QFN-16"  # shown merged all the same


class _OfferOnly:
    """A source that offers a chip for a marking but can't find it by number."""

    name = "offer-only"

    def __init__(self, chip: Chip, *, fail: bool = False) -> None:
        self.chip = chip
        self.fail = fail

    def get(self, mpn: str) -> Chip | None:
        if self.fail:
            raise RuntimeError("get fell over")
        return None

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return match_markings([self.chip], text)


class _Junk:
    """A source whose marking lookup hands back things that aren't matches."""

    name = "junk"

    def __init__(self, *matches: object) -> None:
        self.matches = matches

    def get(self, mpn: str) -> Chip | None:
        return None

    def by_marking(self, text: str) -> list:
        return list(self.matches)


class _Nameless:
    """A broken source that can't even say what it's called."""

    @property
    def name(self) -> str:
        raise RuntimeError("no name")

    def get(self, mpn: str) -> Chip | None:
        raise RuntimeError("get fell over")

    def by_marking(self, text: str) -> list[MarkingMatch]:
        raise RuntimeError("by_marking fell over")


@pytest.mark.parametrize("fail", [False, True])
def test_a_chip_a_source_offers_but_cannot_look_up_is_still_a_candidate(fail: bool) -> None:
    offered = _chip("EX7000QFN", description="Offered", markings=(Marking("X1="),))
    extra = _chip("EX7000QFN", package="QFN-16")
    source = CompositeChipSource(
        [InMemoryChipSource([extra], "local"), _OfferOnly(offered, fail=fail)]
    )
    (match,) = source.by_marking("X1=6A")
    assert match.chip == _chip(
        "EX7000QFN", package="QFN-16", description="Offered", markings=(Marking("X1="),)
    )
    assert [m.chip.mpn for m in CompositeChipSource([_OfferOnly(offered)]).by_marking("X1=")] == [
        "EX7000QFN"
    ]


def _mangled_matches() -> list[object]:
    chip = _chip("EX7000QFN", markings=(Marking("ZZ"),))

    class NoPartNumber:
        key = "ex7000qfn"

    return [
        MarkingMatch(chip, None, MatchKind.PARTIAL, False, 7),  # type: ignore[arg-type]
        MarkingMatch(chip, chip.markings[0], MatchKind.PARTIAL, False, None),  # type: ignore[arg-type]
        MarkingMatch(chip, chip.markings[0], MatchKind.PARTIAL, False, "7"),  # type: ignore[arg-type]
        MarkingMatch(NoPartNumber(), chip.markings[0], MatchKind.PARTIAL, False, 7),  # type: ignore[arg-type]
        MarkingMatch(replace(chip, mpn=123), chip.markings[0], MatchKind.PARTIAL, False, 7),  # type: ignore[arg-type]
        ("EX7000QFN", 7),
        None,
    ]


@pytest.mark.parametrize("junk", _mangled_matches())
def test_a_match_that_is_not_a_match_is_ignored(junk: object) -> None:
    source = CompositeChipSource([_Junk(junk), InMemoryChipSource([_FULL], "community")])
    assert [m.chip for m in source.by_marking("X1=6A")] == [_FULL]
    assert CompositeChipSource([_Junk(junk)]).by_marking("X1=6A") == []


def test_one_mangled_chip_costs_only_the_source_that_gave_it(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")
    good = InMemoryChipSource([_FULL], "community")
    for mangled in (replace(_FULL, functions=None), replace(_FULL, package=20)):  # type: ignore[arg-type]
        bad = InMemoryChipSource([mangled], "local")
        for sources in ([bad, good], [good, bad]):
            source = CompositeChipSource(sources)
            assert source.get("EX1234QFN") == _FULL
            assert source.merged("EX1234QFN").sources == ("community",)
            assert [m.chip for m in source.by_marking("X1=6A")] == [_FULL]
    assert all("local" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("asked", [123, None, ("EX1234QFN",)])
def test_a_part_number_that_is_not_text_is_no_chip(asked: object) -> None:
    source = _composite([_FULL])
    assert source.get(asked) is None  # type: ignore[arg-type]
    assert source.merged(asked) is None  # type: ignore[arg-type]


def test_a_chip_whose_part_number_is_not_text_is_ignored() -> None:
    source = CompositeChipSource(
        [_Wrong(replace(_FULL, mpn=123)), InMemoryChipSource([_FULL], "community")]  # type: ignore[arg-type]
    )
    assert source.get("EX1234QFN") == _FULL


def test_a_source_that_cannot_say_its_name_still_cannot_break_a_lookup(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")
    source = CompositeChipSource([_Nameless(), InMemoryChipSource([_FULL], "community")])
    assert source.get("EX1234QFN") == _FULL
    assert [m.chip for m in source.by_marking("X1=6A")] == [_FULL]
    assert all("_Nameless" in record.getMessage() for record in caplog.records)
    assert caplog.records


def test_candidates_come_back_in_ranked_order_whoever_offered_them() -> None:
    odd = _chip("EX7000QFN", markings=(Marking("ZZ"),))

    class Keen(_OwnMatcher):
        def by_marking(self, text: str) -> list[MarkingMatch]:
            return [replace(m, score=999_999) for m in super().by_marking(text)]

    source = CompositeChipSource([InMemoryChipSource([_FULL], "local"), Keen(odd)])
    assert [m.chip.mpn for m in source.by_marking("X1=6A")] == ["EX7000QFN", "EX1234QFN"]


def test_the_best_score_any_source_offered_is_the_one_kept() -> None:
    odd = _chip("EX7000QFN", markings=(Marking("ZZ"),))

    class Scored(_OwnMatcher):
        def __init__(self, chip: Chip, score: int) -> None:
            super().__init__(chip)
            self.score = score

        def by_marking(self, text: str) -> list[MarkingMatch]:
            return [replace(m, score=self.score) for m in super().by_marking(text)]

    for scores in ((7, 9, 8), (9, 7, 8), (8, 7, 9)):
        source = CompositeChipSource([Scored(odd, score) for score in scores])
        (match,) = source.by_marking("X1=6A")
        assert match.score == 9


def test_a_candidate_shows_the_merged_marking() -> None:
    source = _composite(
        [_chip(markings=(Marking("X1=", examples=("X1=6A",)),))],
        [_chip(markings=(Marking("X1=", examples=("X1=7C",), note="Top line."),))],
    )
    (match,) = source.by_marking("X1=6A")
    assert match.marking == Marking("X1=", examples=("X1=6A", "X1=7C"), note="Top line.")


def test_the_sources_are_held_as_they_were_given() -> None:
    lists = [InMemoryChipSource([_FULL], "local")]
    source = CompositeChipSource(s for s in lists)  # a generator: one pass only
    assert source.get("EX1234QFN") == _FULL
    assert source.get("EX1234QFN") == _FULL
    source = CompositeChipSource(lists)
    lists.clear()  # the caller's list changing later changes nothing
    assert source.get("EX1234QFN") == _FULL
    assert CompositeChipSource(None).sources == ()  # type: ignore[arg-type]
    assert CompositeChipSource(None).get("EX1234QFN") is None  # type: ignore[arg-type]


# --- the start of a part number -------------------------------------------


class _Starts:
    """A source whose part-number search hands back whatever it's given."""

    def __init__(self, *answer: object, name: str = "starts", lookup: bool = True) -> None:
        self.answer = answer
        self.name = name
        self.lookup = lookup

    def get(self, mpn: str) -> Chip | None:
        if not self.lookup:
            return None
        wanted = mpn.strip().casefold()
        return next((c for c in self.answer if isinstance(c, Chip) and c.key == wanted), None)

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return []

    def by_mpn_prefix(self, text: str) -> list:
        return list(self.answer)


def test_no_sources_start_no_part_number() -> None:
    assert CompositeChipSource([]).by_mpn_prefix("EX12") == []


@pytest.mark.parametrize("blank", ["", "   ", None, 7])
def test_nothing_typed_starts_no_part_number(blank: object) -> None:
    assert _composite([_FULL]).by_mpn_prefix(blank) == []  # type: ignore[arg-type]


def test_a_part_number_start_in_two_sources_is_one_candidate_shown_merged() -> None:
    local = _chip(description="Runs hot")
    (found,) = _composite([local], [_FULL]).by_mpn_prefix("ex12")
    assert found == _merge(local, _FULL).chip
    assert found.description == "Runs hot"
    assert found.package == "QFN-20 (3x3)"  # filled from the community source


def test_part_number_starts_from_every_source_are_put_in_one_order() -> None:
    short = _chip("EX12")
    longer = _chip("EX1234QFN-TR")
    later = _chip("EX1299QFN")
    source = _composite([longer, _FULL], [later, short])
    assert [c.mpn for c in source.by_mpn_prefix("EX12")] == [
        "EX12",
        "EX1234QFN",
        "EX1299QFN",
        "EX1234QFN-TR",
    ]
    assert [c.mpn for c in source.by_mpn_prefix("EX129")] == ["EX1299QFN"]


def test_one_source_failing_a_part_number_start_does_not_take_the_others_down(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")
    good = InMemoryChipSource([_FULL], "community")
    # _Broken and _Wrong have no part-number search at all: that's a failure too.
    for broken in (_Broken(), _Wrong(None), _Nameless()):
        for sources in ([broken, good], [good, broken]):
            assert CompositeChipSource(sources).by_mpn_prefix("EX12") == [_FULL]
    assert caplog.records
    assert CompositeChipSource([_Broken(), _Broken()]).by_mpn_prefix("EX12") == []


@pytest.mark.parametrize(
    "junk",
    [
        None,
        "EX1234QFN",
        {"mpn": "EX1234QFN"},
        _chip("EX9000SOT"),  # a chip, but not one the text starts
        _chip(""),  # no part number at all
        replace(_FULL, mpn=7),  # type: ignore[arg-type]
    ],
)
def test_a_part_number_start_that_is_not_one_is_ignored(junk: object) -> None:
    source = CompositeChipSource([_Starts(junk, _FULL), InMemoryChipSource([], "community")])
    assert source.by_mpn_prefix("EX12") == [_FULL]
    assert CompositeChipSource([_Starts(junk)]).by_mpn_prefix("EX12") == []


def test_an_answer_that_is_not_a_list_is_nothing_from_that_source(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")

    class NoList(_Starts):
        def by_mpn_prefix(self, text: str):
            return None

    good = InMemoryChipSource([_FULL], "community")
    assert CompositeChipSource([NoList(), good]).by_mpn_prefix("EX12") == [_FULL]


def test_a_part_number_start_a_source_cannot_look_up_is_still_a_candidate() -> None:
    offered = _chip("EX7000QFN", description="Offered")
    extra = _chip("EX7000QFN", package="QFN-16")
    source = CompositeChipSource(
        [InMemoryChipSource([extra], "local"), _Starts(offered, lookup=False)]
    )
    (found,) = source.by_mpn_prefix("EX70")
    assert found == _chip("EX7000QFN", package="QFN-16", description="Offered")
    assert CompositeChipSource([_Starts(offered, lookup=False)]).by_mpn_prefix("EX7") == [offered]


def test_a_mangled_chip_costs_only_the_source_that_gave_it_a_start(caplog) -> None:
    caplog.set_level(logging.ERROR, logger="benchhud.chips.composite")
    mangled = replace(_FULL, functions=None)  # type: ignore[arg-type]
    other = _chip("EX1299QFN")
    source = CompositeChipSource(
        [InMemoryChipSource([mangled], "local"), InMemoryChipSource([other], "community")]
    )
    assert source.by_mpn_prefix("EX12") == [other]


# --- with real directory sources ------------------------------------------


def test_an_overlay_directory_over_a_built_index(tmp_path) -> None:
    overlay = tmp_path / "chips-private" / "chips" / "example-semi"
    overlay.mkdir(parents=True)
    (overlay / "EX1234QFN.toml").write_text(
        "schema = 1\n"
        'mpn = "EX1234QFN"\n'
        'manufacturer = "Example Semi"\n'
        'description = "Standby buck, runs hot"\n'
        "[[markings]]\n"
        'code = "X1="\n'
        'examples = ["X1=9Z"]\n'
        "[[used_in]]\n"
        'board = "EXAMPLE-BOARD-9"\n'
        'designator = "U1"\n'
    )
    source = CompositeChipSource(
        [
            DirChipSource(root=tmp_path / "chips-private", name="local"),
            DirChipSource(root=FIXTURE, name="community"),
        ]
    )
    merged = source.merged("ex1234qfn")
    assert merged.sources == ("local", "community")
    assert merged.chip.description == "Standby buck, runs hot"
    assert merged.conflict("description").values == (
        ("local", "Standby buck, runs hot"),
        ("community", "Single-phase buck controller for a standby rail"),
    )
    assert (merged.chip.package, merged.chip.package_family) == ("QFN-20 (3x3)", "QFN")
    assert merged.source_of("package") == "community"
    assert merged.chip.markings[0].examples == ("X1=9Z", "X1=6A", "X1=7C")
    assert [u.board for u in merged.chip.used_in] == ["EXAMPLE-BOARD-9", "EXAMPLE-BOARD-1"]
    assert merged.sources_of("used_in") == (("local",), ("community",))

    found = source.by_marking("X1=6A")
    assert [m.chip.mpn for m in found] == ["EX1234QFN", "EX9000SOT"]
    assert found[0].chip == merged.chip
    assert source.get("SD77/NOPB") == DirChipSource(root=FIXTURE).get("SD77/NOPB")
    assert source.by_mpn_prefix("ex") == [merged.chip, source.get("EX9000SOT")]
