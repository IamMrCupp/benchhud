"""Tests for finding a chip from typed text — as a mark and as a part number.

Pure Python: no Qt, no files. Every chip here is made up — "Example Semi"
doesn't exist and neither do its parts or their markings.
"""

from __future__ import annotations

from benchhud.chips.find import Candidate, find_chips
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


def test_part_of_a_part_number_is_not_found() -> None:
    assert find_chips(_source(_BUCK, _LDO), "EX1234") == []


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

    assert [c.chip.mpn for c in find_chips(Repeats(), "X1=")] == ["EX1234QFN"]


def test_a_source_that_answers_none_for_marks_is_no_marks() -> None:
    class Sparse:
        name = "sparse"

        def get(self, mpn: str) -> Chip | None:
            return _BUCK

        def by_marking(self, text: str):
            return None

    assert find_chips(Sparse(), "anything") == [Candidate(_BUCK, by_part_number=True)]
