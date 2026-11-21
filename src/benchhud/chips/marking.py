"""Marking match — from what's printed on a chip to the chips it could be.

A stored ``code`` is the part of the top mark that never changes (``X1=``).
What the operator types is the whole mark (``X1=6A``), or the start of one
while still typing (``X1``). So a match runs both ways: the typed text can
start with the code, or the code can start with the typed text.

Markings are many-to-many — the same three characters turn up on different
chips in different packages — so the answer is always a ranked list of
candidates, each carrying its chip (package, functions) to tell them apart.
Never one silent answer.

The rules, in the order they decide a rank:

1. **A code found whole in the typed text beats a half-typed one.** ``X1=6A``
   against ``X1=`` is a full match; ``X1`` against ``X1=`` is partial.
2. **The typed case beats a folded one.** ``x1=`` still finds ``X1=``, below
   any code that matches as typed. Case on a top mark means something, so it
   counts when it's given and doesn't lock anyone out when it isn't.
3. **Among full matches, the longer code wins** — it accounts for more of the
   mark. Typed text that *is* the code is the longest there can be, so an
   exact match is always first.
4. **Among partial matches, the code with the least left to type wins.**

Whitespace around either side is dropped, and a run of whitespace inside
counts as one space. An empty query, or an empty code, matches nothing: a
marking lookup with nothing typed is not "show me everything".

A marking's ``examples`` are not matched. Every example starts with its code,
so it can't find a chip the code doesn't, and the rest of a mark is a date or
lot code that says nothing about which chip it is.

Pure functions, no Qt, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from benchhud.chips.model import Chip, Marking

# A score is ``band * _BAND + detail``; ``detail`` stays under ``_BAND`` so no
# amount of it lifts a match into the band above.
_BAND = 1000
_FULL_AS_TYPED = 4
_FULL_FOLDED = 3
_PARTIAL_AS_TYPED = 2
_PARTIAL_FOLDED = 1


class MatchKind(Enum):
    """How the typed text met the code — for the caller to label a candidate."""

    #: The typed text is the code.
    EXACT = "exact"
    #: The typed text starts with the code: a whole mark, code then date code.
    WHOLE_MARK = "whole-mark"
    #: The code starts with the typed text: still typing.
    PARTIAL = "partial"


@dataclass(frozen=True)
class MarkingMatch:
    """One candidate for a typed marking.

    ``chip`` carries the package and functions that tell candidates apart;
    ``marking`` is the one that matched. ``score`` is comparable between
    matches for the same typed text, whichever source they came from.
    """

    chip: Chip
    marking: Marking
    kind: MatchKind
    exact_case: bool
    score: int


def normalise_mark(text: str | None) -> str:
    """Trim the ends and squeeze inner whitespace runs to one space."""
    return " ".join((text or "").split())


def _grade(code: str | None, typed: str | None) -> tuple[int, MatchKind, bool] | None:
    """``(score, kind, exact_case)`` for one code against the typed text."""
    c, t = normalise_mark(code), normalise_mark(typed)
    if not c or not t:
        return None
    for exact_case, a, b in ((True, c, t), (False, c.casefold(), t.casefold())):
        if b.startswith(a):  # typed "X1=6A", code "X1="
            band = _FULL_AS_TYPED if exact_case else _FULL_FOLDED
            kind = MatchKind.EXACT if len(a) == len(b) else MatchKind.WHOLE_MARK
            return band * _BAND + min(len(a), _BAND - 1), kind, exact_case
        if a.startswith(b):  # typed "X1", code "X1=" — still typing
            band = _PARTIAL_AS_TYPED if exact_case else _PARTIAL_FOLDED
            left_to_type = len(a) - len(b)
            return band * _BAND + max(0, _BAND - 1 - left_to_type), MatchKind.PARTIAL, exact_case
    return None


def marking_score(code: str | None, typed: str | None) -> int:
    """How well ``typed`` matches ``code``: 0 is no match, higher is better."""
    grade = _grade(code, typed)
    return grade[0] if grade else 0


def match_markings(chips: Iterable[Chip], typed: str | None) -> list[MarkingMatch]:
    """Every chip ``typed`` could be, best first.

    A chip appears once, under its best-matching marking — two codes on one
    chip are still one candidate. Ties keep a fixed order (code, then part
    number) so the same query always gives the same list.
    """
    best: dict[str, MarkingMatch] = {}
    for chip in chips:
        for marking in chip.markings:
            grade = _grade(marking.code, typed)
            if grade is None:
                continue
            score, kind, exact_case = grade
            held = best.get(chip.key)
            if held is None or score > held.score:
                best[chip.key] = MarkingMatch(chip, marking, kind, exact_case, score)
    return sorted(
        best.values(),
        key=lambda m: (-m.score, m.marking.code.casefold(), m.marking.code, m.chip.key),
    )
