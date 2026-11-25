"""Find a chip from what was typed — tried as a mark and as a part number.

At the bench the operator types what's in front of them without saying which
it is: the mark printed on a small part (``X1=6A``), or the part number
printed whole on a bigger one (``EX1234QFN``) — often only the start of it,
because it's long and the candidates are wanted before it's all typed. So the
text goes to the source three ways, and the answers come back as one list:

1. **The chip whose part number it is, first.** A part number is a chip's
   identity, so typing one in full is the strongest thing there is to go on.
2. **Then every chip whose mark the text holds whole** — the code exactly, or
   the code and the date code after it — in the order the source ranked them.
3. **Then everything still being typed**: chips whose code starts with the
   text, and chips whose part number does. They go by how much is left to
   type, least first; a mark goes before a part number with as much left.

That's the marking rule (:mod:`benchhud.chips.marking`) carried across: a
match found whole beats one that's still being typed, and among those still
being typed, the one nearest done wins. The start of a part number is always
still being typed — once it's all there, it's (1).

A chip found more than one way is one candidate, carrying every fact, at the
best place any of them earns it.

A mark is found from its first character on. The start of a part number is
looked up from :data:`MIN_PART_NUMBER_START` characters: before that it would
list every part a maker has. A part number typed in full is found at any
length.

It's still a list, never one answer: a full part number can also be the start
of the mark on some other chip, and both are shown.

No Qt, no I/O of its own. It doesn't catch what a source raises — a source
isn't meant to raise, and the caller that keeps this off the GUI thread is the
one that knows what to say when it does.

**Where a value came from.** When the source puts several sources together
(:class:`~benchhud.chips.composite.CompositeChipSource` over two or more),
:func:`with_provenance` hangs each candidate's
:class:`~benchhud.chips.composite.MergedChip` on it: which source supplied
each value, and where they disagree. With one source there's nothing to tell
apart, and a candidate carries none.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace

from benchhud.chips.composite import MergedChip
from benchhud.chips.marking import MarkingMatch, MatchKind, normalise_mark
from benchhud.chips.model import Chip, mpn_key
from benchhud.chips.source import ChipSource

log = logging.getLogger(__name__)

#: The fewest characters (ends trimmed) the start of a part number is looked
#: up from. One or two would list every part from a maker; a mark is still
#: found from the first.
MIN_PART_NUMBER_START = 3


@dataclass(frozen=True)
class Candidate:
    """One chip the typed text could be, and what made it one.

    ``by_part_number`` says the text is this chip's part number, and
    ``by_part_number_start`` that it's the start of it (never both: the whole
    of it says more). ``match`` is the marking that matched, when one did. At
    least one of the three holds.

    ``merged`` is where each of the chip's values came from, when it was put
    together from several sources — see :func:`with_provenance`.
    """

    chip: Chip
    by_part_number: bool = False
    match: MarkingMatch | None = None
    merged: MergedChip | None = None
    by_part_number_start: bool = False


def _left_to_type(code: str, typed: str) -> int:
    """How much of ``code`` there is still to type after ``typed``."""
    return max(len(normalise_mark(code).casefold()) - len(typed.casefold()), 0)


def find_chips(source: ChipSource, text: str | None) -> list[Candidate]:
    """Every chip ``text`` could be: the part number, whole marks, then the rest.

    See the module docstring for the order. Whitespace is treated the way a
    mark's is (ends trimmed, inner runs as one space). Nothing typed finds
    nothing.
    """
    typed = normalise_mark(text)
    if not typed:
        return []
    wanted = mpn_key(typed)
    by_mpn = source.get(typed)
    matches = list(source.by_marking(typed) or ())
    starts: list[Chip] = []
    if len(wanted) >= MIN_PART_NUMBER_START:
        starts = list(source.by_mpn_prefix(typed) or ())

    # Per chip, by key: the candidate so far and the best rank it has earned.
    # A rank is (tier, left to type, mark before part number, source's order).
    found: dict[str, Candidate] = {}
    rank: dict[str, tuple[int, int, int, int]] = {}

    def earn(key: str, at: tuple[int, int, int, int]) -> None:
        if key not in rank or at < rank[key]:
            rank[key] = at

    if by_mpn is not None and by_mpn.key:
        found[by_mpn.key] = Candidate(by_mpn, by_part_number=True)
        earn(by_mpn.key, (0, 0, 0, 0))
    for order, match in enumerate(matches):
        key = match.chip.key
        if not key:
            continue
        held = found.get(key)
        if held is None:
            found[key] = Candidate(match.chip, match=match)
        elif held.match is None:
            found[key] = replace(held, match=match)
        else:
            continue  # a chip is shown under its best mark, the first offered
        if match.kind is MatchKind.PARTIAL:
            earn(key, (2, _left_to_type(match.marking.code, typed), 0, order))
        else:
            earn(key, (1, 0, 0, order))
    for order, chip in enumerate(starts):
        key = chip.key
        if not key or not key.startswith(wanted):
            continue
        held = found.get(key)
        if held is None:
            found[key] = Candidate(chip, by_part_number_start=True)
        elif not (held.by_part_number or held.by_part_number_start):
            found[key] = replace(held, by_part_number_start=True)
        else:
            continue
        earn(key, (2, len(key) - len(wanted), 1, order))
    return [found[key] for key in sorted(found, key=lambda key: rank[key])]


def tells_sources_apart(source: object) -> bool:
    """Whether ``source`` merges two or more sources and can say which said what."""
    sources = getattr(source, "sources", None)
    return callable(getattr(source, "merged", None)) and (
        isinstance(sources, tuple | list) and len(sources) > 1
    )


def with_provenance(
    source: object, candidates: Iterable[Candidate], limit: int | None = None
) -> list[Candidate]:
    """``candidates`` with each one's :class:`MergedChip` attached, where there is one.

    Only for a source that tells its sources apart; any other gives the
    candidates back as they were. ``limit`` is how many from the top to do it
    for — the ones that will be shown. A candidate whose provenance can't be
    had, or no longer describes the chip in hand, simply goes without: it's
    extra, and the chip is still the chip.
    """
    found = list(candidates)
    if not tells_sources_apart(source):
        return found
    for index, candidate in enumerate(found):
        if limit is not None and index >= limit:
            break
        try:
            merged = source.merged(candidate.chip.mpn)  # type: ignore[attr-defined]
        except Exception:
            log.exception("chips: no provenance for %r", candidate.chip.mpn)
            continue
        if isinstance(merged, MergedChip) and merged.chip == candidate.chip:
            found[index] = replace(candidate, merged=merged)
    return found
