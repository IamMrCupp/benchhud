"""Several chip sources as one — a private overlay over the community data.

:class:`CompositeChipSource` is a :class:`~benchhud.chips.source.ChipSource`
over an ordered list of others. **The order is precedence**: the first source
is the one that wins.

How two sources' chips become one
---------------------------------

- **Identity** is the full part number, case ignored. A chip that two sources
  hold is one chip.
- **A single value** — manufacturer, family, package, description — comes from
  the first source in the order that has one. If a later source says something
  different the chip gets a :class:`Conflict` for that field, holding every
  source's value. Nothing is hidden and nothing is averaged.
- **"Different" ignores case and stray whitespace.** ``QFN-20 (3x3)`` and
  ``qfn-20  (3x3)`` agree; the first source's spelling is the one shown. A
  flag is for two sources that mean different things.
- ``package_family`` **follows the package.** It's derived from the package,
  so it's taken from the sources whose package is the winning one — otherwise
  a ``SOT-23-5`` from one source could wear a ``QFN`` family from another.
- **Lists are joined**, repeats dropped, first source's order first.

What makes two list entries the same one:

==============  =============================================================
functions,      The same slug, case ignored.
sources
markings        The same ``code``, **case kept** — ``aB`` and ``AB`` are
                different marks. Their examples are joined.
documents       They share a SHA-256, **or** they have the same kind and
                filename. A hash is proof; the name is the fallback, because
                the same revision saved by two sites hashes differently. Their
                hashes are joined.
substitutes     The same part number, case ignored.
used_in         The same board and the same designator. Sightings with no
                designator are one entry per board, apart from any with one.
==============  =============================================================

"The same" carries across: if a document shares a hash with a second and the
second shares a name with a third, all three are one document. So which
entries end up together doesn't depend on the order of the sources — only
whose wording is shown does. Repeats inside one source are dropped too.

When entries are the same one, the parts the first lacks (a note, a page
count, a URL) are filled from the next. Those inner fields don't raise
conflicts; only the chip's own single values do.

Provenance
----------

A :class:`~benchhud.chips.model.Chip` stays what it is: immutable, hashable,
no idea where it came from. ``get`` and ``by_marking`` still hand back plain
chips, so nothing that calls them changes.

What a screen needs to say "marking: community · package: local" lives beside
the chip, in a :class:`MergedChip` from :meth:`CompositeChipSource.merged`:
the merged chip, which source supplied each single value, which sources
supplied each list entry, and the conflicts. It is immutable and hashable too.

A marking lookup
----------------

Every source is asked. A chip any of them offers is one candidate, shown as
the merged chip, and the candidates are ranked together by the same rules as
one source's (:mod:`benchhud.chips.marking`).

A part-number search
--------------------

The same: every source is asked, a chip any of them offers is one candidate,
shown as the merged chip, and the candidates are put in one order — the
shortest part number first (:func:`~benchhud.chips.source.mpn_prefix_order`).

A source that fails
-------------------

Sources promise not to raise. If one does anyway it's logged with its
traceback and counted as having nothing; the others still answer. The same
goes for a source that hands back something that isn't a chip, or a chip too
mangled to merge: that source's answer is dropped, not everyone's.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from benchhud.chips.marking import MarkingMatch, match_markings, normalise_mark
from benchhud.chips.model import Chip, Document, Marking, Substitute, UsedIn, mpn_key
from benchhud.chips.source import ChipSource, mpn_prefix_order

log = logging.getLogger(__name__)

#: A chip's own single values, merged first-source-wins. ``package_family``
#: isn't here: it goes with the package.
SCALAR_FIELDS = ("mpn", "manufacturer", "family", "package", "description")

#: A chip's lists, merged by joining.
LIST_FIELDS = ("functions", "sources", "markings", "documents", "substitutes", "used_in")


def _fold(text: str | None) -> str:
    """Text as it's compared: ends trimmed, inner runs one space, case folded."""
    return " ".join((text or "").split()).casefold()


def same_text(a: str | None, b: str | None) -> bool:
    """Whether two values agree — case and stray whitespace don't count."""
    return _fold(a) == _fold(b)


@dataclass(frozen=True)
class Conflict:
    """Sources that disagree about one of a chip's single values.

    ``values`` is every source that has a value for ``field``, in the
    configured order, as ``(source name, value)``. The first is the one the
    merged chip uses.
    """

    field: str
    values: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MergedChip:
    """A chip put together from one or more sources, and where each part came from.

    ``chip`` is the merged chip — what ``get`` returns. ``sources`` names every
    source that holds the chip, in the configured order.
    """

    chip: Chip
    sources: tuple[str, ...]
    #: ``(field, source name)`` for each single value the chip has.
    scalar_sources: tuple[tuple[str, str], ...] = ()
    #: ``(field, per-entry source names)``; the entries line up with the list
    #: on ``chip``.
    item_sources: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = ()
    conflicts: tuple[Conflict, ...] = ()

    def source_of(self, field_name: str) -> str | None:
        """The source that supplied a single value, or ``None`` if the chip has none."""
        return next((source for name, source in self.scalar_sources if name == field_name), None)

    def sources_of(self, field_name: str) -> tuple[tuple[str, ...], ...]:
        """For a list field, the sources behind each entry, in the list's order.

        ``zip(merged.chip.markings, merged.sources_of("markings"))`` pairs each
        marking with the sources that have it.
        """
        return next((sources for name, sources in self.item_sources if name == field_name), ())

    def conflict(self, field_name: str) -> Conflict | None:
        """The conflict on a field, if its sources disagree."""
        return next((c for c in self.conflicts if c.field == field_name), None)


# --- merging --------------------------------------------------------------

_Found = Sequence[tuple[str, Chip]]


def _scalar(found: _Found, name: str) -> tuple[str | None, str | None, Conflict | None]:
    """``(value, its source, conflict)`` for one single value, first source wins."""
    values = [(source, value) for source, chip in found if _fold(value := getattr(chip, name))]
    if not values:
        return None, None, None
    source, winner = values[0]
    disagree = any(not same_text(value, winner) for _, value in values[1:])
    return winner, source, Conflict(name, tuple(values)) if disagree else None


def _join[T](
    found: _Found, name: str, same: Callable[[T, T], bool], fill: Callable[[T, T], T]
) -> tuple[tuple[T, ...], tuple[tuple[str, ...], ...]]:
    """One list from every source's: ``(entries, the sources behind each)``.

    ``same`` says whether two entries are the same one; ``fill`` returns the
    first with whatever it lacks taken from the second.

    Entries are gathered into groups of "the same one". A newcomer joins every
    group that holds a match for it, and groups it bridges become one — so
    the grouping is the same whatever order the sources come in. Each group is
    then one entry: its earliest member, filled from the rest in order.
    """
    # Each group: its members as (arrival, source, entry), in arrival order.
    groups: list[list[tuple[int, str, T]]] = []
    arrival = 0
    for source, chip in found:
        for entry in getattr(chip, name):
            member = (arrival, source, entry)
            arrival += 1
            hits = [i for i, group in enumerate(groups) if any(same(m[2], entry) for m in group)]
            if not hits:
                groups.append([member])
                continue
            home = groups[hits[0]]
            home.append(member)
            for i in reversed(hits[1:]):  # the newcomer bridges these into the first
                home.extend(groups.pop(i))
            home.sort(key=lambda m: m[0])
    entries: list[T] = []
    behind: list[tuple[str, ...]] = []
    for group in groups:
        entry = group[0][2]
        for _, _, other in group[1:]:
            entry = fill(entry, other)
        entries.append(entry)
        behind.append(tuple(dict.fromkeys(source for _, source, _ in group)))
    return tuple(entries), tuple(behind)


def _unique(first: Sequence[str], second: Sequence[str]) -> tuple[str, ...]:
    """Both, in order, each value once (compared as written)."""
    return tuple(dict.fromkeys((*first, *second)))


def _first[T](a: T, _b: T) -> T:
    return a


def _same_marking(a: Marking, b: Marking) -> bool:
    return normalise_mark(a.code) == normalise_mark(b.code)


def _fill_marking(a: Marking, b: Marking) -> Marking:
    return Marking(code=a.code, examples=_unique(a.examples, b.examples), note=a.note or b.note)


def _hashes(document: Document) -> set[str]:
    return {h.strip().lower() for h in document.sha256 if h.strip()}


def _same_document(a: Document, b: Document) -> bool:
    if _hashes(a) & _hashes(b):
        return True
    return same_text(a.kind, b.kind) and same_text(a.filename, b.filename)


def _fill_document(a: Document, b: Document) -> Document:
    # Each hash once, however it's cased, in the first spelling seen.
    hashes: dict[str, str] = {}
    for written in (*a.sha256, *b.sha256):
        if written.strip():
            hashes.setdefault(written.strip().lower(), written)
    return Document(
        kind=a.kind,
        filename=a.filename,
        pages=a.pages if a.pages is not None else b.pages,
        sha256=tuple(hashes.values()),
        url=a.url or b.url,
        note=a.note or b.note,
    )


def _same_substitute(a: Substitute, b: Substitute) -> bool:
    return mpn_key(a.mpn) == mpn_key(b.mpn)


def _fill_substitute(a: Substitute, b: Substitute) -> Substitute:
    return Substitute(mpn=a.mpn, note=a.note or b.note)


def _same_use(a: UsedIn, b: UsedIn) -> bool:
    return same_text(a.board, b.board) and same_text(a.designator, b.designator)


def _fill_use(a: UsedIn, b: UsedIn) -> UsedIn:
    return UsedIn(board=a.board, designator=a.designator, role=a.role or b.role)


_JOINS: dict[str, tuple[Callable[[Any, Any], bool], Callable[[Any, Any], Any]]] = {
    "functions": (same_text, _first),
    "sources": (same_text, _first),
    "markings": (_same_marking, _fill_marking),
    "documents": (_same_document, _fill_document),
    "substitutes": (_same_substitute, _fill_substitute),
    "used_in": (_same_use, _fill_use),
}


def merge_chips(found: _Found) -> MergedChip:
    """One chip from the same chip in several sources.

    ``found`` is ``(source name, chip)`` in the configured order, every chip
    the same part number — two spellings of it agree like any other text, so
    they aren't a conflict. Raises ``ValueError`` when it's empty: there is no
    chip to make.
    """
    if not found:
        raise ValueError("no chips to merge")
    values: dict[str, Any] = {}
    scalar_sources: list[tuple[str, str]] = []
    conflicts: list[Conflict] = []

    def take(name: str, among: _Found) -> None:
        value, source, conflict = _scalar(among, name)
        values[name] = value
        if source is not None:
            scalar_sources.append((name, source))
        if conflict is not None:
            conflicts.append(conflict)

    for name in SCALAR_FIELDS:
        take(name, found)
    # The family belongs to the package that won, not to whoever spoke first.
    take("package_family", [(s, c) for s, c in found if same_text(c.package, values["package"])])

    item_sources: list[tuple[str, tuple[tuple[str, ...], ...]]] = []
    for name in LIST_FIELDS:
        same, fill = _JOINS[name]
        entries, behind = _join(found, name, same, fill)
        values[name] = entries
        item_sources.append((name, behind))

    # A part number and a maker are what a chip can't be without; if every
    # source left one blank, the first source's blank is what there is.
    first = found[0][1]
    values["mpn"] = values["mpn"] or first.mpn
    values["manufacturer"] = values["manufacturer"] or first.manufacturer
    return MergedChip(
        chip=Chip(**values),
        sources=tuple(dict.fromkeys(source for source, _ in found)),
        scalar_sources=tuple(scalar_sources),
        item_sources=tuple(item_sources),
        conflicts=tuple(conflicts),
    )


# --- the source -----------------------------------------------------------


def _name_of(source: object) -> str:
    try:
        return str(source.name)  # type: ignore[attr-defined]
    except Exception:
        return type(source).__name__


def _sound(match: object) -> bool:
    """Whether a source's marking match is one this can rank and show."""
    try:
        return (
            isinstance(match, MarkingMatch)
            and isinstance(match.chip, Chip)
            and isinstance(match.marking, Marking)
            and type(match.score) is int
            and bool(match.chip.key)
        )
    except Exception:
        return False


def _starts(chip: object, wanted: str) -> bool:
    """Whether a source's part-number answer is a chip that starts with ``wanted``."""
    try:
        return isinstance(chip, Chip) and bool(chip.key) and chip.key.startswith(wanted)
    except Exception:
        return False


@dataclass
class CompositeChipSource:
    """Several sources as one. ``sources`` is in order of precedence, first wins."""

    sources: Sequence[ChipSource] = field(default_factory=tuple)
    name: str = "chips"

    def __post_init__(self) -> None:
        # Held as a tuple: a list the caller keeps editing, or a generator
        # that's empty the second time round, would change the answer.
        self.sources = tuple(self.sources or ())

    def _holding(self, mpn: str, offered: dict[int, Chip] | None = None) -> list[tuple[str, Chip]]:
        """``(source name, chip)`` from every source that has this part number.

        ``offered`` is chips some sources have already handed over for it, by
        the source's place in the order — used when a source's own lookup by
        part number comes back empty for a chip it just offered.
        """
        if not isinstance(mpn, str) or not (wanted := mpn_key(mpn)):
            return []
        found: list[tuple[str, Chip]] = []
        for place, source in enumerate(self.sources):
            name = _name_of(source)
            chip = None
            try:
                answer = source.get(mpn)
                # Anything but this chip is a source's mistake, not an answer.
                if isinstance(answer, Chip) and answer.key == wanted:
                    chip = answer
            except Exception:
                log.exception("chips: source %r failed looking up a part number", name)
            if chip is None:
                chip = (offered or {}).get(place)
            if chip is None:
                continue
            try:
                merge_chips([(name, chip)])
            except Exception:
                # A chip too mangled to merge costs only the source that gave it.
                log.exception("chips: source %r gave a chip that can't be merged", name)
                continue
            found.append((name, chip))
        return found

    def _merge(self, mpn: str, offered: dict[int, Chip] | None = None) -> MergedChip | None:
        found = self._holding(mpn, offered)
        if not found:
            return None
        try:
            return merge_chips(found)
        except Exception:
            # Each chip merged on its own; together they didn't.
            log.exception("chips: merging %r from %s failed", mpn, [name for name, _ in found])
            return None

    def reload(self) -> None:
        """Have every source that reads from somewhere read it again, now.

        This is the slow part of a lookup, done ahead of time: call it from a
        worker thread and the first lookup finds everything already in memory.
        """
        for source in self.sources:
            again = getattr(source, "reload", None)
            if not callable(again):
                continue
            try:
                again()
            except Exception:
                log.exception("chips: source %r failed reloading", _name_of(source))

    def merged(self, mpn: str) -> MergedChip | None:
        """The chip with this part number, merged, with where each part came from."""
        return self._merge(mpn)

    def get(self, mpn: str) -> Chip | None:
        merged = self.merged(mpn)
        return merged.chip if merged is not None else None

    def by_marking(self, text: str) -> list[MarkingMatch]:
        # Per chip: the best match any source offered, and each source's chip.
        best: dict[str, MarkingMatch] = {}
        offered: dict[str, dict[int, Chip]] = {}
        for place, source in enumerate(self.sources):
            try:
                matches = [match for match in source.by_marking(text) if _sound(match)]
            except Exception:
                log.exception("chips: source %r failed looking up a marking", _name_of(source))
                continue
            for match in matches:
                key = match.chip.key
                offered.setdefault(key, {}).setdefault(place, match.chip)
                if key not in best or match.score > best[key].score:
                    best[key] = match
        chips = {
            key: merged.chip
            for key, match in best.items()
            if (merged := self._merge(match.chip.mpn, offered[key])) is not None
        }
        # Ranked on the merged chips, so a candidate shows under the best
        # marking any source has for it.
        ranked = {match.chip.key: match for match in match_markings(chips.values(), text)}
        # A source with a matcher of its own may offer a chip this one wouldn't
        # have; its word is kept, on the merged chip.
        for key, chip in chips.items():
            if key not in ranked:
                ranked[key] = replace(best[key], chip=chip)
        return sorted(ranked.values(), key=lambda match: match.sort_key)

    def by_mpn_prefix(self, text: str) -> list[Chip]:
        if not isinstance(text, str) or not (wanted := mpn_key(text)):
            return []
        # Per chip: what each source offered, by its place in the order.
        offered: dict[str, dict[int, Chip]] = {}
        for place, source in enumerate(self.sources):
            try:
                chips = [chip for chip in source.by_mpn_prefix(text) if _starts(chip, wanted)]
            except Exception:
                log.exception(
                    "chips: source %r failed looking up the start of a part number",
                    _name_of(source),
                )
                continue
            for chip in chips:
                offered.setdefault(chip.key, {}).setdefault(place, chip)
        found = [
            merged.chip
            for by_place in offered.values()
            if (merged := self._merge(next(iter(by_place.values())).mpn, by_place)) is not None
        ]
        return sorted(found, key=mpn_prefix_order)
