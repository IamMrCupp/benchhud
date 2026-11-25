"""Pluggable chip sources.

benchhud is a *reader* of chip reference data, not its home. The data lives in
a chip-data checkout or a private overlay on disk, and later several of those
merged in a configured order. The seam mirrors
:mod:`benchhud.inventory.source`: a Protocol, a Null default and an in-memory
fake, so a directory reader is just the first real backend and the HUD runs
fine with none.

The one difference from the inventory seam: this one is **read-only**. There
is no write path, so nothing here raises. A source that can't answer returns
nothing (and logs a line, when it has a reason to) — a dead source never
wedges a lookup.

No source runs git or reaches the network.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from benchhud.chips.marking import MarkingMatch, match_markings
from benchhud.chips.model import Chip, mpn_key


@runtime_checkable
class ChipSource(Protocol):
    """Something benchhud can look chips up in."""

    @property
    def name(self) -> str:
        """Human label for the source — shown in the UI, and what a merge of
        several sources records as where a value came from."""
        ...

    def get(self, mpn: str) -> Chip | None:
        """The chip with this full part number, compared without regard to case."""
        ...

    def by_marking(self, text: str) -> list[MarkingMatch]:
        """Candidates for a typed top mark, best first — never one silent answer.

        See :mod:`benchhud.chips.marking` for what matches and how it ranks.
        """
        ...

    def by_mpn_prefix(self, text: str) -> list[Chip]:
        """Chips whose part number starts with ``text``, the shortest first.

        Compared the way part numbers are (:func:`~benchhud.chips.model.mpn_key`):
        ends trimmed, case ignored. A part number that *is* the text counts.
        Nothing typed finds nothing. A source answers whatever length it's
        given; how short is too short to ask is the caller's call. See
        :func:`mpn_prefix_order` for the order.
        """
        ...


def mpn_prefix_order(chip: Chip) -> tuple[int, str]:
    """Where a chip goes in a part-number search: least left to type first.

    The typed text is the start of every one, so the shortest part number is
    the one it's closest to. Ties go by part number, so the order is fixed.
    """
    key = chip.key
    return len(key), key


def match_mpn_prefix(chips: Iterable[Chip], text: str | None) -> list[Chip]:
    """Every chip whose part number starts with ``text``, in
    :func:`mpn_prefix_order`.

    A part number that's in ``chips`` twice is the first one, the same as a
    lookup by part number; a chip with no part number is never found. A plain
    walk of the list — a source with a lot of chips keeps them sorted and
    finds the run by bisecting instead.
    """
    wanted = mpn_key(text)
    if not wanted:
        return []
    found: dict[str, Chip] = {}
    for chip in chips:
        key = chip.key
        if key and key.startswith(wanted):
            found.setdefault(key, chip)
    return sorted(found.values(), key=mpn_prefix_order)


class NullChipSource:
    """Default: no chip data configured — benchhud runs without it."""

    name = "none"

    def get(self, mpn: str) -> Chip | None:
        return None

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return []

    def by_mpn_prefix(self, text: str) -> list[Chip]:
        return []


@dataclass
class InMemoryChipSource:
    """A source backed by a list — for tests, demos, and offline fallback.

    If two chips in the list share a part number, the first one is the chip,
    for every lookup. A chip with a blank part number is never found.
    """

    chips: list[Chip] = field(default_factory=list)
    name: str = "in-memory"

    def get(self, mpn: str) -> Chip | None:
        wanted = mpn_key(mpn)
        if not wanted:
            return None
        return next((c for c in self.chips if c.key == wanted), None)

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return match_markings(self.chips, text)

    def by_mpn_prefix(self, text: str) -> list[Chip]:
        return match_mpn_prefix(self.chips, text)
