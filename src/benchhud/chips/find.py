"""Find a chip from what was typed — tried as a mark and as a part number.

At the bench the operator types what's in front of them without saying which
it is: the mark printed on a small part (``X1=6A``), or the part number
printed whole on a bigger one (``EX1234QFN``) — often only the start of it,
because it's long and the candidates are wanted before it's all typed. So the
text is looked up as a mark, as a whole part number and as the start of one,
and the answers come back as one list:

1. **The chip whose part number it is, first.** A part number is a chip's
   identity, so typing one in full is the strongest thing there is to go on.
2. **Then the marks, in the order the source ranked them**
   (:mod:`benchhud.chips.marking`), **with the starts of part numbers merged
   in by how near they are.**

Near is how many characters stand between what was typed and the candidate:

- for the start of a part number, how much is left to type;
- for a mark, how far the code's length is from the text's — what's left to
  type of a code still being typed, or what was typed past a code found
  whole (``X1=6A`` is two past ``X1=``). A code that *is* the text is 0.

A start goes ahead of the next mark when it's nearer; on a tie the mark goes
first. The marks never change order among themselves — a code found whole
still beats one still being typed, as the marking rules say — so a start can
only go *between* them. One thing follows from that: a short code found whole
holds back the marks after it. Typing ``AB12`` in a library with a chip coded
``A`` (three typed past it) lists ``AB12Q`` and ``AB12QF`` ahead of ``A``, and
so ahead of a code ``AB12X`` behind it, though that's one from done. Only
part numbers nearer than the shortest code found whole can pass it, which at
the bench is a few rows. The other way round — ``A`` ahead of ``AB12Q`` — is
the bug this order exists to avoid.

Why: a short code turns up whole at the start of nearly anything, so
``TPS62130`` lists ``TPS62130RGT`` (one left to type) ahead of chips coded
``T`` or ``TP``, which are seven and six characters short of what was typed.
And a mark one key from done (``EXC`` typed, code ``EXCQ``) isn't buried under
every part number that starts ``EXC``: those all have more left to type.

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
from benchhud.chips.marking import MarkingMatch, normalise_mark
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


def _length(text: str) -> int:
    return len(normalise_mark(text).casefold())


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
    typed_length = _length(typed)
    whole = source.get(typed)
    if whole is not None and whole.key != wanted:
        whole = None  # not the part number asked for: a source's mistake, not an answer
    matches = [match for match in source.by_marking(typed) or () if match.chip.key]
    starts: list[Chip] = []
    if not short_of_part_number_start(typed):
        starts = [chip for chip in source.by_mpn_prefix(typed) or () if chip.key.startswith(wanted)]
    if whole is None:
        # A source whose lookup by part number missed it, but whose search by
        # the start found it: it's still the part number that was typed.
        whole = next((chip for chip in starts if chip.key == wanted), None)
    starts.sort(key=lambda chip: len(chip.key))  # nearest first; ties as the source gave them

    def mark_distance(match: MarkingMatch) -> int:
        return abs(_length(match.marking.code) - typed_length)

    # The marks in their own order, the starts merged in by distance.
    merged: list[tuple[Chip, MarkingMatch | None]] = []
    m = n = 0
    while m < len(matches) or n < len(starts):
        take_mark = n == len(starts) or (
            m < len(matches) and mark_distance(matches[m]) <= len(starts[n].key) - len(wanted)
        )
        if take_mark:
            merged.append((matches[m].chip, matches[m]))
            m += 1
        else:
            merged.append((starts[n], None))
            n += 1

    # One candidate per chip, where it first appears, carrying every fact.
    found: dict[str, Candidate] = {}
    if whole is not None:
        found[whole.key] = Candidate(whole, by_part_number=True)
    for chip, match in merged:
        key = chip.key
        held = found.get(key)
        if match is not None:
            if held is None:
                found[key] = Candidate(chip, match=match)
            elif held.match is None:
                found[key] = replace(held, match=match)
            # else: a chip is shown under its best mark, the first offered
        elif held is None:
            found[key] = Candidate(chip, by_part_number_start=True)
        elif not (held.by_part_number or held.by_part_number_start):
            found[key] = replace(held, by_part_number_start=True)
    return list(found.values())


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
