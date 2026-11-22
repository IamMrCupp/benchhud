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
"""

from __future__ import annotations

from dataclasses import dataclass

from benchhud.chips.marking import MarkingMatch, normalise_mark
from benchhud.chips.model import Chip
from benchhud.chips.source import ChipSource


@dataclass(frozen=True)
class Candidate:
    """One chip the typed text could be, and what made it one.

    ``by_part_number`` says the text is this chip's part number; ``match`` is
    the marking that matched, when one did. At least one of the two holds.
    """

    chip: Chip
    by_part_number: bool = False
    match: MarkingMatch | None = None


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
