"""Find a chip from what was typed — tried as a mark and as a part number.

At the bench the operator types what's in front of them without saying which
it is: the mark printed on a small part (``X1=6A``), or the part number
printed whole on a bigger one (``EX1234QFN``) — often only the start of it,
because it's long and the candidates are wanted before it's all typed. So the
text is looked up as a mark, as a whole part number and as the start of one,
and the answers come back as one list:

1. **The chip whose part number it is.** A part number is a chip's identity,
   so typing one in full is the strongest thing there is to go on.
2. **Every chip whose code is exactly what was typed.**
3. **Every chip whose part number starts with it**, least left to type first.
4. **Every chip whose code the text holds whole**, with more typed after it —
   a date code, or the rest of something that isn't a mark at all.
5. **Every chip whose code starts with it**: a mark still being typed.

Marks keep the order the source ranked them in (:mod:`benchhud.chips.marking`),
so (2), (4) and (5) are that order. What this adds is where a part number
goes. The rule is the marking module's own, carried across: what accounts
for more of the typed text ranks higher. The start of a part number accounts
for all of it; a code found whole with more typed after it accounts for only
the code. So ``TPS62130`` lists ``TPS62130RGT`` above a chip whose code is
``TP``. A code that *is* the text accounts for all of it with nothing left to
type, so it stays above every start.

A chip found more than one way is one candidate, carrying every fact, at the
best place any of them earns it.

A mark is found from its first character on. The start of a part number is
looked up from :data:`MIN_PART_NUMBER_START` characters as typed: before that
it would list every part a maker has. A part number typed in full is found at
any length.

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

#: The fewest characters (ends trimmed, counted as typed, before case is
#: folded) the start of a part number is looked up from. One or two would
#: list every part from a maker; a mark is still found from the first.
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


def short_of_part_number_start(text: str | None) -> bool:
    """Whether ``text`` is too short to be looked up as the start of a part number."""
    return len(normalise_mark(text)) < MIN_PART_NUMBER_START


def find_chips(source: ChipSource, text: str | None) -> list[Candidate]:
    """Every chip ``text`` could be, best first.

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
    if not short_of_part_number_start(typed):
        starts = list(source.by_mpn_prefix(typed) or ())

    # Per chip, by key: the candidate so far and the best rank it has earned.
    # A rank is (place in the module docstring's list, the source's order):
    # the source already puts its marks best first and its starts least left
    # to type first, so within a place its order is the order.
    found: dict[str, Candidate] = {}
    rank: dict[str, tuple[int, int]] = {}

    def earn(key: str, at: tuple[int, int]) -> None:
        if key not in rank or at < rank[key]:
            rank[key] = at

    if by_mpn is not None and by_mpn.key:
        found[by_mpn.key] = Candidate(by_mpn, by_part_number=True)
        earn(by_mpn.key, (1, 0))
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
        # (4) and (5) share a place: the source ranks a code found whole
        # above one still being typed.
        earn(key, (2 if match.kind is MatchKind.EXACT else 4, order))
    for order, chip in enumerate(starts):
        key = chip.key
        if not key or not key.startswith(wanted):
            continue
        held = found.get(key)
        if held is not None and (held.by_part_number or held.by_part_number_start):
            continue
        if key == wanted:
            # The whole part number, from a source whose lookup by part number
            # missed it: it's what was typed, so it's (1), not a start.
            fact, at = {"by_part_number": True}, (1, 0)
        else:
            fact, at = {"by_part_number_start": True}, (3, order)
        found[key] = Candidate(chip, **fact) if held is None else replace(held, **fact)
        earn(key, at)
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
