"""The chip model — one IC as the chip reference knows it.

The fields line up, name for name, with one record in the chip-data repo's
``index.json`` (format version 1): the same keys, ``None`` where the record
has ``null`` and an empty tuple where it has ``[]``. Turning a record into a
:class:`Chip` belongs to the source that reads the file, not to the model.

Lists are tuples so a chip is immutable all the way down and hashable — a
source can hand the same object to every caller, and the merge can compare
values from two sources without copying them.

Every field is generic reference: what a part is, what's printed on it, which
document describes it, which board designs use it. There is no job, customer
or engagement field, on purpose. ``used_in`` names a board design, never whose
machine it was.
"""

from __future__ import annotations

from dataclasses import dataclass


def mpn_key(mpn: str | None) -> str:
    """The form of a part number two chips are compared by.

    A chip's identity is its full part number without regard to case, so
    ``EX1234QFN``, ``ex1234qfn`` and ``" EX1234QFN "`` are one chip. Blank or
    missing gives ``""``, which matches nothing.
    """
    return (mpn or "").strip().casefold()


@dataclass(frozen=True)
class Marking:
    """What's printed on the part.

    ``code`` is the part of the top mark that never changes (``X1=``);
    ``examples`` are whole marks as read off real parts (``X1=6A``). The rest
    of a mark is usually a date or lot code, so it says nothing about which
    chip it is.
    """

    code: str
    examples: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class Document:
    """A document's fingerprint — never the document.

    ``sha256`` holds several hashes because the same revision saved by two
    sites can differ byte for byte. ``kind`` is ``datasheet``,
    ``example-circuit`` or ``app-note`` today; a consumer must cope with one
    it has never seen.
    """

    kind: str
    filename: str
    pages: int | None = None
    sha256: tuple[str, ...] = ()
    url: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Substitute:
    """Another part number that can stand in, and what to check first."""

    mpn: str
    note: str | None = None


@dataclass(frozen=True)
class UsedIn:
    """A sighting on a board design, by the number printed on the board."""

    board: str
    designator: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class Chip:
    """One chip, keyed by its full part number.

    ``package_family`` is worked out by the chip-data build (``QFN`` for
    ``QFN-20 (3x3)``); it's ``None`` from a source that doesn't supply it.
    ``functions`` are slugs from chip-data's vocabulary, and ``sources`` is who
    the entry is credited to.
    """

    mpn: str
    manufacturer: str
    family: str | None = None
    package: str | None = None
    package_family: str | None = None
    functions: tuple[str, ...] = ()
    description: str | None = None
    sources: tuple[str, ...] = ()
    markings: tuple[Marking, ...] = ()
    documents: tuple[Document, ...] = ()
    substitutes: tuple[Substitute, ...] = ()
    used_in: tuple[UsedIn, ...] = ()

    @property
    def key(self) -> str:
        """The part number as identity — see :func:`mpn_key`."""
        return mpn_key(self.mpn)
