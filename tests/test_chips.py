"""Tests for the chip model, the ChipSource seam and marking match.

Pure Python: no Qt, no files, no network. Every chip here is made up —
"Example Semi" doesn't exist and neither do its parts or their markings.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from benchhud.chips import (
    Chip,
    ChipSource,
    Document,
    InMemoryChipSource,
    Marking,
    MarkingMatch,
    MatchKind,
    NullChipSource,
    Substitute,
    UsedIn,
    marking_score,
    match_markings,
    mpn_key,
    normalise_mark,
)

# One record as chip-data's index.json (format 1) carries it: every key always
# there, null or [] when the chip file left it out.
_RECORD = {
    "mpn": "EX1234QFN",
    "manufacturer": "Example Semi",
    "family": "EX1234",
    "package": "QFN-20 (3x3)",
    "package_family": "QFN",
    "functions": ["buck-controller"],
    "description": "Single-phase buck controller for a standby rail",
    "sources": ["bench-notes"],
    "markings": [{"code": "X1=", "examples": ["X1=6A", "X1=7C"], "note": None}],
    "documents": [
        {
            "kind": "datasheet",
            "filename": "EX1234.pdf",
            "pages": 14,
            "sha256": ["0123456789abcdef" * 4],
            "url": "https://example.com/ex1234",
            "note": None,
        }
    ],
    "substitutes": [{"mpn": "EX1235QFN", "note": None}],
    "used_in": [{"board": "EXAMPLE-BOARD-1", "designator": "U7000", "role": "standby 3.3 V"}],
}

_BUCK = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    family="EX1234",
    package="QFN-20 (3x3)",
    package_family="QFN",
    functions=("buck-controller",),
    description="Single-phase buck controller for a standby rail",
    sources=("bench-notes",),
    markings=(Marking(code="X1=", examples=("X1=6A", "X1=7C")),),
    documents=(
        Document(
            kind="datasheet",
            filename="EX1234.pdf",
            pages=14,
            sha256=("0123456789abcdef" * 4,),
            url="https://example.com/ex1234",
        ),
    ),
    substitutes=(Substitute(mpn="EX1235QFN"),),
    used_in=(UsedIn(board="EXAMPLE-BOARD-1", designator="U7000", role="standby 3.3 V"),),
)


def _chip(mpn: str, *codes: str, package: str = "QFN-20 (3x3)", function: str = "x") -> Chip:
    """A made-up chip with these marking codes."""
    return Chip(
        mpn=mpn,
        manufacturer="Example Semi",
        package=package,
        functions=(function,),
        markings=tuple(Marking(code=c) for c in codes),
    )


def _names(cls) -> set[str]:
    return {f.name for f in fields(cls)}


# --- model ---------------------------------------------------------------


def test_chip_fields_are_the_index_record_keys() -> None:
    assert _names(Chip) == set(_RECORD)
    assert _names(Marking) == set(_RECORD["markings"][0])
    assert _names(Document) == set(_RECORD["documents"][0])
    assert _names(Substitute) == set(_RECORD["substitutes"][0])
    assert _names(UsedIn) == set(_RECORD["used_in"][0])


def test_the_smallest_chip_is_a_part_number_and_a_maker() -> None:
    chip = Chip(mpn="EX1234QFN", manufacturer="Example Semi")
    assert chip.family is None
    assert chip.package is None
    assert chip.package_family is None
    assert chip.description is None
    assert chip.functions == ()
    assert chip.sources == ()
    assert chip.markings == ()
    assert chip.documents == ()
    assert chip.substitutes == ()
    assert chip.used_in == ()


def test_nested_records_default_to_empty() -> None:
    assert Marking(code="X1=") == Marking(code="X1=", examples=(), note=None)
    doc = Document(kind="datasheet", filename="EX1234.pdf")
    assert (doc.pages, doc.sha256, doc.url, doc.note) == (None, (), None, None)
    assert Substitute(mpn="EX1235QFN").note is None
    used = UsedIn(board="EXAMPLE-BOARD-1")
    assert (used.designator, used.role) == (None, None)


def test_a_chip_is_immutable_and_hashable() -> None:
    with pytest.raises(FrozenInstanceError):
        _BUCK.package = "SOP-8"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        _BUCK.markings[0].code = "Y2"  # type: ignore[misc]
    twin = replace(_BUCK)
    assert twin is not _BUCK
    assert len({_BUCK, twin}) == 1


def test_a_chip_holds_nothing_about_a_job_or_a_customer() -> None:
    every_field = (
        _names(Chip) | _names(Marking) | _names(Document) | _names(Substitute) | _names(UsedIn)
    )
    for word in ("customer", "owner", "job", "ticket", "engagement", "serial"):
        assert not [name for name in every_field if word in name]


@pytest.mark.parametrize("written", ["EX1234QFN", "ex1234qfn", "Ex1234Qfn", "  EX1234QFN\t"])
def test_identity_is_the_part_number_whatever_its_case(written: str) -> None:
    assert mpn_key(written) == mpn_key("EX1234QFN")
    assert Chip(mpn=written, manufacturer="Example Semi").key == _BUCK.key


def test_different_part_numbers_are_different_chips() -> None:
    assert mpn_key("EX1234QFN") != mpn_key("EX1234SOP")


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_part_number_has_an_empty_key(blank) -> None:
    assert mpn_key(blank) == ""


# --- the seam ------------------------------------------------------------


def test_both_sources_satisfy_the_protocol() -> None:
    for source in (NullChipSource(), InMemoryChipSource()):
        assert isinstance(source, ChipSource)


def test_null_source_finds_nothing() -> None:
    source = NullChipSource()
    assert source.name == "none"
    assert source.get("EX1234QFN") is None
    assert source.by_marking("X1=6A") == []


def test_in_memory_source_names_itself() -> None:
    assert InMemoryChipSource().name == "in-memory"
    assert InMemoryChipSource(name="local").name == "local"


def test_an_empty_in_memory_source_finds_nothing() -> None:
    source = InMemoryChipSource()
    assert source.get("EX1234QFN") is None
    assert source.by_marking("X1=") == []


@pytest.mark.parametrize("asked", ["EX1234QFN", "ex1234qfn", " Ex1234qfn "])
def test_get_ignores_case_and_surrounding_whitespace(asked: str) -> None:
    assert InMemoryChipSource([_BUCK]).get(asked) is _BUCK


def test_get_wants_the_full_part_number() -> None:
    source = InMemoryChipSource([_BUCK])
    assert source.get("EX1234") is None  # the family, not a part number
    assert source.get("EX1234QFN-TR") is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_get_with_nothing_typed_is_none(blank: str) -> None:
    assert InMemoryChipSource([Chip(mpn="", manufacturer="Example Semi"), _BUCK]).get(blank) is None


def test_get_takes_the_first_of_two_chips_with_one_part_number() -> None:
    first = Chip(mpn="EX1234QFN", manufacturer="Example Semi", package="QFN-20 (3x3)")
    second = Chip(mpn="ex1234qfn", manufacturer="Example Semi", package="SOP-8")
    assert InMemoryChipSource([first, second]).get("EX1234QFN") is first


def test_by_marking_returns_candidates_with_package_and_function() -> None:
    (match,) = InMemoryChipSource([_BUCK]).by_marking("X1=6A")
    assert isinstance(match, MarkingMatch)
    assert match.chip is _BUCK
    assert match.chip.package == "QFN-20 (3x3)"
    assert match.chip.functions == ("buck-controller",)
    assert match.marking.code == "X1="


def test_one_marking_on_two_chips_gives_both() -> None:
    qfn = _chip("EX1234QFN", "X1=", package="QFN-20 (3x3)", function="buck-controller")
    sot = _chip("EX9000SOT", "X1=", package="SOT-23-5", function="ldo")
    found = InMemoryChipSource([sot, qfn]).by_marking("X1=6A")
    assert [m.chip.mpn for m in found] == ["EX1234QFN", "EX9000SOT"]
    assert [m.chip.package for m in found] == ["QFN-20 (3x3)", "SOT-23-5"]
    assert [m.chip.functions for m in found] == [("buck-controller",), ("ldo",)]


# --- marking match: what matches -----------------------------------------


def test_typing_the_code_is_an_exact_match() -> None:
    (match,) = match_markings([_BUCK], "X1=")
    assert match.kind is MatchKind.EXACT
    assert match.exact_case


def test_a_whole_mark_longer_than_the_code_matches() -> None:
    (match,) = match_markings([_BUCK], "X1=6A")
    assert match.kind is MatchKind.WHOLE_MARK
    assert match.marking.code == "X1="


def test_a_half_typed_mark_still_finds_candidates() -> None:
    (match,) = match_markings([_BUCK], "X1")
    assert match.kind is MatchKind.PARTIAL
    (match,) = match_markings([_BUCK], "X")
    assert match.kind is MatchKind.PARTIAL


@pytest.mark.parametrize(
    ("code", "typed"),
    [
        ("X1=", "X2"),
        ("X1=", "X1-6A"),  # agrees for two characters, then doesn't
        ("X1=7", "X1=6A"),
        ("X1=", "1="),  # a mark is read from its start
        ("X1=", "AX1="),
    ],
)
def test_a_mark_that_parts_ways_with_the_code_is_no_match(code: str, typed: str) -> None:
    assert marking_score(code, typed) == 0
    assert match_markings([_chip("EX1234QFN", code)], typed) == []


def test_examples_are_not_what_gets_matched() -> None:
    chip = Chip(
        mpn="EX1234QFN",
        manufacturer="Example Semi",
        markings=(Marking(code="X1=", examples=("X1=6A",)),),
    )
    assert match_markings([chip], "X1=6A")[0].kind is MatchKind.WHOLE_MARK
    assert match_markings([chip], "6A") == []


# --- marking match: case, whitespace, nothing typed ----------------------


def test_case_is_folded_when_it_has_to_be() -> None:
    (match,) = match_markings([_BUCK], "x1=6a")
    assert match.kind is MatchKind.WHOLE_MARK
    assert not match.exact_case


def test_between_codes_of_one_length_the_typed_case_wins() -> None:
    upper = _chip("EX1000QFN", "AB")
    lower = _chip("EX2000QFN", "ab")
    assert [m.chip.mpn for m in match_markings([upper, lower], "ab7")] == [
        "EX2000QFN",
        "EX1000QFN",
    ]
    assert [m.chip.mpn for m in match_markings([lower, upper], "AB7")] == [
        "EX1000QFN",
        "EX2000QFN",
    ]


def test_case_never_outweighs_length() -> None:
    # Typed in lower case at the bench: the whole code still beats one letter of it.
    whole = _chip("EX1000QFN", "X1=")
    one_letter = _chip("EX2000QFN", "x")
    found = match_markings([one_letter, whole], "x1=")
    assert [(m.chip.mpn, m.kind, m.exact_case) for m in found] == [
        ("EX1000QFN", MatchKind.EXACT, False),
        ("EX2000QFN", MatchKind.WHOLE_MARK, True),
    ]
    nearer = _chip("EX3000QFN", "X1=")
    further = _chip("EX4000QFN", "x1=6A")
    assert [m.chip.mpn for m in match_markings([further, nearer], "x1")] == [
        "EX3000QFN",
        "EX4000QFN",
    ]


def test_folding_that_changes_a_length_still_grades_right() -> None:
    eszett = _chip("EX1000QFN", "ß1")  # "ß" folds to "ss"
    (match,) = match_markings([eszett], "SS1")
    assert (match.kind, match.exact_case) == (MatchKind.EXACT, False)
    (match,) = match_markings([eszett], "SS1X")
    assert match.kind is MatchKind.WHOLE_MARK
    (match,) = match_markings([eszett], "S")
    assert match.kind is MatchKind.PARTIAL


def test_a_folded_full_match_outranks_a_half_typed_one() -> None:
    folded = _chip("EX1000QFN", "ab")
    partial = _chip("EX2000QFN", "ABC")
    found = match_markings([partial, folded], "AB")
    assert [(m.chip.mpn, m.kind) for m in found] == [
        ("EX1000QFN", MatchKind.EXACT),
        ("EX2000QFN", MatchKind.PARTIAL),
    ]


@pytest.mark.parametrize("typed", ["  X1=6A", "X1=6A  ", "\tX1=6A\n"])
def test_whitespace_around_the_typed_mark_is_dropped(typed: str) -> None:
    assert marking_score("X1=", typed) == marking_score("X1=", "X1=6A")


def test_whitespace_around_a_stored_code_is_dropped() -> None:
    assert marking_score(" X1= ", "X1=6A") == marking_score("X1=", "X1=6A")


def test_a_run_of_inner_whitespace_counts_as_one_space() -> None:
    assert normalise_mark("  AB \t 12  ") == "AB 12"
    assert marking_score("AB 12", "AB   12") == marking_score("AB 12", "AB 12")
    assert marking_score("AB 12", "AB12") == 0  # a space is still a character


@pytest.mark.parametrize("nothing", ["", "   ", "\t", None])
def test_nothing_typed_matches_nothing(nothing) -> None:
    assert marking_score("X1=", nothing) == 0
    assert match_markings([_BUCK], nothing) == []


@pytest.mark.parametrize("nothing", ["", "   ", "\t"])
def test_a_source_asked_about_nothing_gives_nothing(nothing: str) -> None:
    assert InMemoryChipSource([_BUCK]).by_marking(nothing) == []


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_an_empty_code_matches_nothing(empty) -> None:
    assert marking_score(empty, "X1=6A") == 0
    assert match_markings([_chip("EX1234QFN", empty)], "X1=6A") == []


# --- marking match: ranking ----------------------------------------------


def test_exact_beats_a_shorter_code_beats_a_half_typed_one() -> None:
    exact = _chip("EX1000QFN", "X1=")
    shorter = _chip("EX2000QFN", "X1")
    longer = _chip("EX3000QFN", "X1=6")
    found = match_markings([longer, shorter, exact], "X1=")
    assert [(m.chip.mpn, m.kind) for m in found] == [
        ("EX1000QFN", MatchKind.EXACT),
        ("EX2000QFN", MatchKind.WHOLE_MARK),
        ("EX3000QFN", MatchKind.PARTIAL),
    ]
    assert [m.score for m in found] == sorted((m.score for m in found), reverse=True)


def test_a_typed_mark_longer_than_the_code_ranks_the_longest_code_first() -> None:
    chips = [_chip("EX2000QFN", "X1"), _chip("EX1000QFN", "X1="), _chip("EX3000QFN", "X1=6A")]
    found = match_markings(chips, "X1=6A")
    assert [m.marking.code for m in found] == ["X1=6A", "X1=", "X1"]
    assert [m.kind for m in found] == [
        MatchKind.EXACT,
        MatchKind.WHOLE_MARK,
        MatchKind.WHOLE_MARK,
    ]


def test_half_typed_ranks_the_code_with_least_left_to_type_first() -> None:
    chips = [_chip("EX1000QFN", "X1=6A"), _chip("EX2000QFN", "X1=")]
    assert [m.marking.code for m in match_markings(chips, "X1")] == ["X1=", "X1=6A"]
    # Not just alphabetical: "X1Z" sorts after "X1=6A" but has less left to type.
    chips = [_chip("EX1000QFN", "X1=6A"), _chip("EX2000QFN", "X1Z")]
    assert [m.marking.code for m in match_markings(chips, "X1")] == ["X1Z", "X1=6A"]


def test_a_chip_is_one_candidate_under_its_best_marking() -> None:
    chip = _chip("EX1234QFN", "X1", "X1=")
    (match,) = match_markings([chip], "X1=6A")
    assert match.marking.code == "X1="
    (match,) = match_markings([_chip("EX1234QFN", "X1=", "X1")], "X1=6A")
    assert match.marking.code == "X1="


def test_a_part_number_listed_twice_is_the_first_one_for_both_lookups() -> None:
    first = _chip("EX1234QFN", "X1")
    second = _chip("ex1234qfn", "X1=")  # the better match, but not the chip
    source = InMemoryChipSource([first, second])
    (match,) = source.by_marking("X1=6A")
    assert match.chip is first
    assert match.marking.code == "X1"
    assert source.get("EX1234QFN") is first


def test_a_chip_with_no_part_number_is_not_a_candidate() -> None:
    nameless = [_chip("", "X1="), _chip("  ", "X1=")]
    assert match_markings(nameless, "X1=") == []
    assert [m.chip.mpn for m in match_markings([*nameless, _BUCK], "X1=")] == ["EX1234QFN"]


def test_ties_come_back_in_the_same_order_every_time() -> None:
    a, b, c = _chip("EX1000QFN", "X1="), _chip("EX2000QFN", "X1="), _chip("EX3000QFN", "X1=")
    expected = ["EX1000QFN", "EX2000QFN", "EX3000QFN"]
    assert [m.chip.mpn for m in match_markings([c, a, b], "X1=")] == expected
    assert [m.chip.mpn for m in match_markings([b, c, a], "X1=")] == expected


def test_a_tie_goes_by_code_before_part_number() -> None:
    # Both fold to what was typed, so they score the same. "AB" sorts before
    # "ab"; the part numbers are the other way round.
    upper = _chip("EX2000QFN", "AB")
    lower = _chip("EX1000QFN", "ab")
    for chips in ([upper, lower], [lower, upper]):
        found = match_markings(chips, "Ab")
        assert found[0].score == found[1].score
        assert [m.marking.code for m in found] == ["AB", "ab"]


def test_matches_from_two_sources_sort_into_one_ranked_list() -> None:
    local = InMemoryChipSource([_chip("EX2000QFN", "X1"), _chip("EX4000QFN", "X1=6")])
    community = InMemoryChipSource([_chip("EX1000QFN", "X1="), _chip("EX3000QFN", "X1")])
    together = sorted(
        local.by_marking("X1=") + community.by_marking("X1="), key=lambda m: m.sort_key
    )
    assert [m.chip.mpn for m in together] == ["EX1000QFN", "EX2000QFN", "EX3000QFN", "EX4000QFN"]
    everything = match_markings([*local.chips, *community.chips], "X1=")
    assert together == everything


def test_score_is_zero_for_no_match_and_positive_otherwise() -> None:
    assert marking_score("X1=", "Y2") == 0
    for typed in ("X1=", "X1=6A", "X1", "x1"):
        assert marking_score("X1=", typed) > 0


def test_no_amount_of_length_lifts_a_half_typed_match_above_a_full_one() -> None:
    weakest_full = marking_score("a", "A")  # one character, case folded
    assert marking_score("ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNO") < weakest_full
    assert marking_score("X1", "X") < weakest_full
    huge = "Z" * 5000
    assert 0 < marking_score(huge, "z") < marking_score(huge, "Z") < weakest_full
    assert marking_score(huge, huge.lower()) > weakest_full


# --- the start of a part number -------------------------------------------


def test_null_source_finds_no_part_number_start() -> None:
    assert NullChipSource().by_mpn_prefix("EX12") == []


def test_the_start_of_a_part_number_finds_it_whatever_the_case() -> None:
    source = InMemoryChipSource([_BUCK])
    for typed in ("EX12", "ex12", " Ex1234q ", "EX1234QFN"):
        assert source.by_mpn_prefix(typed) == [_BUCK]


def test_only_the_start_counts_not_the_middle() -> None:
    source = InMemoryChipSource([_BUCK])
    assert source.by_mpn_prefix("1234") == []
    assert source.by_mpn_prefix("QFN") == []
    assert source.by_mpn_prefix("EX1234QFN-TR") == []


@pytest.mark.parametrize("blank", ["", "   "])
def test_nothing_typed_starts_no_part_number(blank: str) -> None:
    assert InMemoryChipSource([_BUCK]).by_mpn_prefix(blank) == []


def test_the_shortest_part_number_comes_first_then_by_part_number() -> None:
    long = _chip("EX1234QFN-TR", "Q1")
    other = _chip("EX1299QFN", "Q2")
    short = _chip("EX12", "Q3")
    found = InMemoryChipSource([long, _BUCK, other, short]).by_mpn_prefix("ex12")
    assert [c.mpn for c in found] == ["EX12", "EX1234QFN", "EX1299QFN", "EX1234QFN-TR"]


def test_a_part_number_start_takes_the_first_of_two_chips_with_one_part_number() -> None:
    again = _chip("ex1234qfn", "Q9", package="SOP-8")
    source = InMemoryChipSource([_BUCK, again])
    assert source.by_mpn_prefix("EX12") == [_BUCK]
    assert source.by_mpn_prefix("EX12") == [source.get("EX1234QFN")]


def test_a_chip_with_no_part_number_starts_nothing() -> None:
    blank = Chip(mpn="  ", manufacturer="Example Semi")
    assert InMemoryChipSource([blank, _BUCK]).by_mpn_prefix("E") == [_BUCK]
