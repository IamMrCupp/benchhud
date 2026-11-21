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


class NullChipSource:
    """Default: no chip data configured — benchhud runs without it."""

    name = "none"

    def get(self, mpn: str) -> Chip | None:
        return None

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return []


@dataclass
class InMemoryChipSource:
    """A source backed by a list — for tests, demos, and offline fallback.

    If two chips in the list share a part number, the first one is the chip,
    for both lookups. A chip with a blank part number is never found.
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
