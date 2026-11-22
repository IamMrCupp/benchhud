"""Find a chip from what was typed — tried as a mark and as a part number.

At the bench the operator types what's in front of them without saying which
it is: the mark printed on a small part (``X1=6A``), or the part number
printed whole on a bigger one (``EX1234QFN``). So the text goes to the source
both ways, and the answers come back as one list:

1. **The chip whose part number it is, first.** A part number is a chip's
   identity, so typing one in full is the strongest thing there is to go on.
2. **Then every chip the text could be as a mark**, in the order the source
   ranked them.

A chip found both ways is one candidate, at the top, carrying both facts.

A part number is only found when it's typed in full —
:class:`~benchhud.chips.source.ChipSource` has ``get`` and no search — while a
mark is found from its first character on.

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
from benchhud.chips.model import Chip
from benchhud.chips.source import ChipSource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    """One chip the typed text could be, and what made it one.

    ``by_part_number`` says the text is this chip's part number; ``match`` is
    the marking that matched, when one did. At least one of the two holds.

    ``merged`` is where each of the chip's values came from, when it was put
    together from several sources — see :func:`with_provenance`.
    """

    chip: Chip
    by_part_number: bool = False
    match: MarkingMatch | None = None
    merged: MergedChip | None = None


def find_chips(source: ChipSource, text: str | None) -> list[Candidate]:
    """Every chip ``text`` could be: the part-number hit, then the marks, ranked.

    Whitespace is treated the way a mark's is (ends trimmed, inner runs as one
    space). Nothing typed finds nothing.
    """
    typed = normalise_mark(text)
    if not typed:
        return []
    by_mpn = source.get(typed)
    matches = list(source.by_marking(typed) or ())

    found: list[Candidate] = []
    seen: set[str] = set()
    if by_mpn is not None and by_mpn.key:
        same = next((m for m in matches if m.chip.key == by_mpn.key), None)
        found.append(Candidate(by_mpn, by_part_number=True, match=same))
        seen.add(by_mpn.key)
    for match in matches:
        key = match.chip.key
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(Candidate(match.chip, match=match))
    return found


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
