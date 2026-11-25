"""Tests for finding a chip from typed text — as a mark and as a part number.

Pure Python: no Qt, no files. Every chip here is made up — "Example Semi"
doesn't exist and neither do its parts or their markings.
"""

from __future__ import annotations

from dataclasses import replace

from benchhud.chips.find import MIN_PART_NUMBER_START, Candidate, find_chips
from benchhud.chips.marking import MatchKind
from benchhud.chips.model import Chip, Marking
from benchhud.chips.source import InMemoryChipSource, NullChipSource

_BUCK = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    package="QFN-20 (3x3)",
    functions=("buck-controller",),
    markings=(Marking("X1=", ("X1=6A",)),),
)
_LDO = Chip(
    mpn="EX9000SOT",
    manufacturer="Example Semi",
    package="SOT-23-5",
    functions=("ldo",),
    markings=(Marking("X1=", ("X1=2B",)),),
)
# Its part number is printed whole on the package, so its code starts one.
_BIG = Chip(mpn="EX7700BGA", manufacturer="Example Semi", markings=(Marking("EX7700"),))
# A chip whose code is another chip's whole part number.
_CLASH = Chip(mpn="EX0001DFN", manufacturer="Example Semi", markings=(Marking("EX1234QFN"),))


def _source(*chips: Chip) -> InMemoryChipSource:
    return InMemoryChipSource(list(chips))


def test_nothing_typed_finds_nothing() -> None:
    source = _source(_BUCK)
    assert find_chips(source, "") == []
    assert find_chips(source, "   ") == []
    assert find_chips(source, None) == []


def test_a_source_with_no_data_finds_nothing() -> None:
    assert find_chips(NullChipSource(), "X1=") == []


def test_a_mark_finds_every_chip_that_carries_it() -> None:
    found = find_chips(_source(_BUCK, _LDO), "X1=6A")
    assert [c.chip.mpn for c in found] == ["EX1234QFN", "EX9000SOT"]
    assert all(not c.by_part_number for c in found)
    assert all(c.match is not None and c.match.kind is MatchKind.WHOLE_MARK for c in found)


def test_a_full_part_number_finds_its_chip_whatever_the_case() -> None:
    found = find_chips(_source(_BUCK, _LDO), " ex1234qfn ")
    assert found == [Candidate(_BUCK, by_part_number=True)]


def test_the_start_of_a_part_number_finds_its_chip() -> None:
    assert find_chips(_source(_BUCK, _LDO), "ex1234") == [
        Candidate(_BUCK, by_part_number_start=True)
    ]


def test_a_chip_found_both_ways_is_one_candidate() -> None:
    (only,) = find_chips(_source(_BIG), "EX7700BGA")
    assert only.chip is _BIG
    assert only.by_part_number
    assert only.match is not None
    assert only.match.kind is MatchKind.WHOLE_MARK


def test_the_part_number_hit_comes_before_the_marks() -> None:
    # The mark match is exact — the best a mark can do — and still second.
    found = find_chips(_source(_CLASH, _BUCK), "EX1234QFN")
    assert [c.chip.mpn for c in found] == ["EX1234QFN", "EX0001DFN"]
    assert [c.by_part_number for c in found] == [True, False]
    assert found[1].match is not None
    assert found[1].match.kind is MatchKind.EXACT


def test_marks_keep_the_order_the_source_ranked_them() -> None:
    longer = Chip(mpn="EX1235QFN", manufacturer="Example Semi", markings=(Marking("X1=6"),))
    source = _source(_BUCK, longer, _LDO)
    found = find_chips(source, "X1=6A")
    assert [c.match for c in found] == source.by_marking("X1=6A")
    assert found[0].chip is longer  # the longer code accounts for more of the mark


def test_a_part_number_a_source_repeats_is_listed_once() -> None:
    class Repeats:
        name = "repeats"

        def get(self, mpn: str) -> Chip | None:
            return None

        def by_marking(self, text: str):
            once = _source(_BUCK).by_marking(text)
            return once + once

        def by_mpn_prefix(self, text: str) -> list[Chip]:
            return []

    assert [c.chip.mpn for c in find_chips(Repeats(), "X1=")] == ["EX1234QFN"]


def test_a_source_that_answers_none_for_marks_is_no_marks() -> None:
    class Sparse:
        name = "sparse"

        def get(self, mpn: str) -> Chip | None:
            return _BUCK

        def by_marking(self, text: str):
            return None

        def by_mpn_prefix(self, text: str):
            return None

    assert find_chips(Sparse(), "ex1234qfn") == [Candidate(_BUCK, by_part_number=True)]


# --- the start of a part number ---------------------------------------------


class _Asked(InMemoryChipSource):
    """Records every part-number search it's asked for."""

    def __init__(self, *chips: Chip) -> None:
        super().__init__(list(chips))
        self.asked: list[str] = []

    def by_mpn_prefix(self, text: str) -> list[Chip]:
        self.asked.append(text)
        return super().by_mpn_prefix(text)


def test_a_part_number_start_is_not_looked_up_below_the_floor() -> None:
    assert MIN_PART_NUMBER_START == 3
    source = _Asked(_BUCK)
    assert find_chips(source, "EX") == []
    assert find_chips(source, " E ") == []
    assert source.asked == []
    assert find_chips(source, "EX1") == [Candidate(_BUCK, by_part_number_start=True)]
    assert source.asked == ["EX1"]


def test_a_full_part_number_shorter_than_the_floor_is_still_found() -> None:
    tiny = Chip(mpn="Q1", manufacturer="Example Semi")
    assert find_chips(_source(tiny), "q1") == [Candidate(tiny, by_part_number=True)]


def test_a_mark_is_still_found_from_its_first_character() -> None:
    assert [c.chip.mpn for c in find_chips(_source(_BUCK, _LDO), "X")] == [
        "EX1234QFN",
        "EX9000SOT",
    ]


def test_starts_of_part_numbers_go_least_left_to_type_first() -> None:
    long = Chip(mpn="EX1234QFN-TR", manufacturer="Example Semi")
    found = find_chips(_source(long, _LDO, _BUCK), "ex1")
    assert [c.chip.mpn for c in found] == ["EX1234QFN", "EX1234QFN-TR"]
    found = find_chips(_source(long, _LDO, _BUCK), "EX")  # under the floor
    assert found == []
    assert all(c.by_part_number_start and not c.by_part_number for c in found)


def test_the_full_part_number_is_not_also_its_own_start() -> None:
    longer = Chip(mpn="EX1234QFN-TR", manufacturer="Example Semi")
    found = find_chips(_source(longer, _BUCK), "EX1234QFN")
    assert found == [
        Candidate(_BUCK, by_part_number=True),
        Candidate(longer, by_part_number_start=True),
    ]


def test_a_chip_found_by_its_start_and_its_mark_is_one_candidate() -> None:
    # "EX77" starts the part number EX7700BGA and the code EX7700.
    (only,) = find_chips(_source(_BIG), "EX77")
    assert only.chip is _BIG
    assert only.by_part_number_start
    assert not only.by_part_number
    assert only.match is not None
    assert only.match.kind is MatchKind.PARTIAL


# Made-up chips for ranking, each found a different way by "AB12".
_EXACT = Chip(mpn="ZZ0001", manufacturer="Example Semi", markings=(Marking("AB12"),))
_WHOLE = Chip(mpn="ZZ0002", manufacturer="Example Semi", markings=(Marking("AB1"),))
_MARK_1_LEFT = Chip(mpn="ZZ0003", manufacturer="Example Semi", markings=(Marking("AB12X"),))
_MARK_4_LEFT = Chip(mpn="ZZ0004", manufacturer="Example Semi", markings=(Marking("AB12WXYZ"),))
_START_1_LEFT = Chip(mpn="AB12Q", manufacturer="Example Semi")
_START_2_LEFT = Chip(mpn="AB12QF", manufacturer="Example Semi")
_START_6_LEFT = Chip(mpn="AB12QFN-TR", manufacturer="Example Semi")
_FULL_MPN = Chip(mpn="AB12", manufacturer="Example Semi")


def test_how_a_part_number_start_ranks_against_the_marks() -> None:
    source = _source(
        _START_6_LEFT,
        _MARK_4_LEFT,
        _START_2_LEFT,
        _WHOLE,
        _START_1_LEFT,
        _MARK_1_LEFT,
        _EXACT,
        _FULL_MPN,
    )
    found = find_chips(source, "AB12")
    # Each line: how near it is to "AB12".
    assert [c.chip.mpn for c in found] == [
        "AB12",  # the part number, typed in full
        "ZZ0001",  # 0: the code exactly
        "ZZ0002",  # 1: the code "AB1", found whole with one typed past it
        "ZZ0003",  # 1: the code "AB12X", one left — a mark goes first on a tie
        "AB12Q",  # 1 left to type
        "AB12QF",  # 2
        "ZZ0004",  # 4: the code "AB12WXYZ"
        "AB12QFN-TR",  # 6
    ]
    assert [c.by_part_number_start for c in found] == [
        False,
        False,
        False,
        False,
        True,
        True,
        False,
        True,
    ]


def test_a_long_part_number_is_not_buried_under_short_codes() -> None:
    # Codes of one to three characters are what SOT-23 parts carry. Each is
    # found whole in a long part number, but accounts for little of it.
    wanted = Chip(mpn="EX62130RGTR", manufacturer="Example Semi")
    codes = [
        Chip(mpn=f"ZZ{n}", manufacturer="Example Semi", markings=(Marking(code),))
        for n, code in enumerate(("E", "EX", "EX6"))
    ]
    found = find_chips(_source(*codes, wanted), "EX62130RGT")
    assert [c.chip.mpn for c in found] == ["EX62130RGTR", "ZZ2", "ZZ1", "ZZ0"]


def test_marks_keep_the_source_order_around_the_starts() -> None:
    source = _source(_START_1_LEFT, _MARK_1_LEFT, _WHOLE, _EXACT)
    marks = [c.chip for c in find_chips(source, "AB12") if not c.by_part_number_start]
    assert marks == [m.chip for m in source.by_marking("AB12")]


def test_a_chip_found_two_ways_ranks_at_the_better_of_them() -> None:
    # Its code is exactly what was typed, so it ranks as an exact mark even
    # though its part number has a lot left to type.
    exact = Chip(mpn="AB12QFN-TR", manufacturer="Example Semi", markings=(Marking("AB12"),))
    found = find_chips(_source(_START_1_LEFT, exact), "AB12")
    assert [c.chip.mpn for c in found] == ["AB12QFN-TR", "AB12Q"]
    assert found[0].by_part_number_start
    assert found[0].match is not None
    assert found[0].match.kind is MatchKind.EXACT
    # A code with more left to type ranks by its part number instead.
    late = Chip(mpn="AB12Z", manufacturer="Example Semi", markings=(Marking("AB12WXYZ"),))
    found = find_chips(_source(_START_2_LEFT, _MARK_1_LEFT, late), "AB12")
    assert [c.chip.mpn for c in found] == ["ZZ0003", "AB12Z", "AB12QF"]
    assert found[1].match is not None


def test_the_floor_counts_characters_as_typed_not_as_folded() -> None:
    # "ß" folds to "ss" and "ﬃ" to "ffi": still one character typed.
    source = _Asked(Chip(mpn="SS1234", manufacturer="Example Semi"))
    assert find_chips(source, "ß1") == []
    assert find_chips(source, "ﬃ") == []
    assert source.asked == []
    assert [c.chip.mpn for c in find_chips(source, "ß12")] == ["SS1234"]
    assert source.asked == ["ß12"]


def test_a_whole_part_number_found_only_as_a_start_is_the_part_number() -> None:
    class NoGet(InMemoryChipSource):
        def get(self, mpn: str) -> Chip | None:
            return None

    other = Chip(mpn="ZZ0009", manufacturer="Example Semi", markings=(Marking("EX12"),))
    found = find_chips(NoGet([other, _BUCK]), "ex1234qfn")
    assert found[0] == Candidate(_BUCK, by_part_number=True)
    assert [c.chip.mpn for c in found] == ["EX1234QFN", "ZZ0009"]


def test_a_candidate_is_never_both_the_part_number_and_its_start() -> None:
    # A careless get that answers for less than the whole part number isn't
    # the part number; the chip is one candidate, found by its start.
    class Careless(InMemoryChipSource):
        def get(self, mpn: str) -> Chip | None:
            return _BUCK

    (only,) = find_chips(Careless([_BUCK]), "EX12")
    assert only == Candidate(_BUCK, by_part_number_start=True)


def test_a_start_a_source_gets_wrong_is_not_listed() -> None:
    class Loose(InMemoryChipSource):
        def by_mpn_prefix(self, text: str) -> list[Chip]:
            return [_LDO, Chip(mpn="", manufacturer="Example Semi"), _BUCK]

    assert find_chips(Loose([]), "EX12") == [Candidate(_BUCK, by_part_number_start=True)]


def test_a_source_that_answers_none_for_starts_is_no_starts() -> None:
    class Sparse(InMemoryChipSource):
        def by_mpn_prefix(self, text: str):
            return None

    assert find_chips(Sparse([_BUCK]), "X1=") == find_chips(_source(_BUCK), "X1=")


def test_a_get_answer_that_is_not_the_part_number_typed_is_ignored() -> None:
    class Wrong(InMemoryChipSource):
        def get(self, mpn: str) -> Chip | None:
            return _LDO

    assert find_chips(Wrong([_BUCK, _LDO]), "EX1234QFN") == [Candidate(_BUCK, by_part_number=True)]
    assert find_chips(Wrong([]), "ZZZ") == []


def test_a_mark_one_key_from_done_is_not_buried_under_part_numbers() -> None:
    marked = Chip(mpn="ZZ0001", manufacturer="Example Semi", markings=(Marking("EXCQ"),))
    whole = Chip(mpn="ZZ0002", manufacturer="Example Semi", markings=(Marking("EX"),))
    family = [Chip(mpn=f"EXC{n:04}EDD", manufacturer="Example Semi") for n in range(250)]
    found = find_chips(_source(*family, whole, marked), "EXC")
    assert [c.chip.mpn for c in found[:2]] == ["ZZ0002", "ZZ0001"]
    assert len(found) == 252


def test_starts_go_nearest_first_whatever_order_the_source_gave() -> None:
    class Backwards(InMemoryChipSource):
        def by_mpn_prefix(self, text: str) -> list[Chip]:
            return list(reversed(super().by_mpn_prefix(text)))

    source = Backwards([_START_6_LEFT, _START_1_LEFT, _START_2_LEFT, _MARK_4_LEFT])
    assert [c.chip.mpn for c in find_chips(source, "AB12")] == [
        "AB12Q",
        "AB12QF",
        "ZZ0004",
        "AB12QFN-TR",
    ]


def test_a_chip_offered_twice_keeps_the_first_mark_it_was_offered_under() -> None:
    class Twice(InMemoryChipSource):
        def by_marking(self, text: str):
            best, worse = _source(_BUCK).by_marking("X1=6A"), _source(_SUB_CODE).by_marking("X1")
            return [*best, replace(worse[0], chip=_BUCK)]

    (only,) = find_chips(Twice([]), "X1=6A")
    assert only.match is not None
    assert only.match.marking.code == "X1="


_SUB_CODE = Chip(mpn="EX0002", manufacturer="Example Semi", markings=(Marking("X1"),))
