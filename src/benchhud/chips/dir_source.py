"""A :class:`ChipSource` backed by a directory on disk.

The directory is one you keep, like ``~/boards``: a chip-data checkout, a
build of one unpacked somewhere, or a private overlay in the same shape.
benchhud doesn't run git and doesn't fetch anything — it reads what's there.

Two shapes are read:

- **A built index** — ``index.json`` (format 1), as ``chipdata build`` writes
  it: beside the directory's other files, or in ``out/`` of a checkout. The
  path may also be the ``index.json`` file itself.
- **Chip files** — ``chips/<manufacturer>/<part-number>.toml``, one per chip.

Which one gets read
-------------------

The index, when there is one and it can be trusted; the chip files otherwise.

- In a checkout the chip files are the source and ``out/index.json`` is made
  from them, so **an index older than anything under** ``chips/`` **or**
  ``vocab/`` **is skipped** and the chip files are read. Otherwise a
  ``git pull`` without a rebuild would leave the HUD on last week's data with
  nothing to say so.
- **An index in a format newer than 1 is never guessed at.** It isn't used. If
  the directory also has chip files those are read — each carries its own
  ``schema`` number and is checked the same way; if it doesn't, nothing loads.
- An index that can't be read or isn't the right shape is treated the same.

Every one of those writes a log line saying which file and why.

What the chip-file path does about the build's work
---------------------------------------------------

The build adds two things a chip file doesn't hold, both from the checkout's
own ``vocab/`` files, and this reader does the same so both paths give the
same :class:`Chip`:

- ``package_family`` — the family in ``vocab/packages.toml`` the package
  starts with, longest first.
- A function slug listed under ``[deprecated]`` in ``vocab/functions.toml``
  is replaced by its successor.

An overlay with no ``vocab/`` still loads: ``package_family`` is ``None`` and
slugs stay as written.

A reader, not a validator
-------------------------

It takes what it can use. Unknown keys are ignored, and a function slug or a
document ``kind`` it has never seen is carried through. A chip it can't make
sense of — no part number, a list where text belongs, a ``schema`` it doesn't
know — is skipped with one log line and the rest still load. ``chipdata
validate`` is the check; this never raises.

A part number that appears twice is the first one read, the same rule as
:class:`~benchhud.chips.source.InMemoryChipSource`: index order in an index,
sorted path order among chip files (which is the file chip-data's own
validator keeps).

When the directory is read
--------------------------

Not at construction — building the source costs nothing and touches no disk.
The first lookup reads it, and the result is kept in memory. Nothing watches
the directory: :meth:`DirChipSource.reload` re-reads it, and so does a
restart. A lookup that arrives during a reload answers from the copy already
held, never a half-read one.

The first lookup does the reading, so the caller decides which thread pays for
it — call ``reload()`` from a worker before the first lookup to keep it off
the GUI thread. Loading is guarded by a lock, so that is safe.
"""

from __future__ import annotations

import json
import logging
import threading
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from benchhud.chips.marking import MarkingMatch, match_markings
from benchhud.chips.model import Chip, Document, Marking, Substitute, UsedIn, mpn_key

log = logging.getLogger(__name__)

#: The published format this reads — the ``format`` key of ``index.json``.
INDEX_FORMAT = 1

#: The chip-file version this reads — the ``schema`` key of a chip file.
CHIP_SCHEMA = 1

INDEX_NAME = "index.json"

# A family must be followed by one of these (or nothing) to be the package's
# family: "QFN-20", "QFN (3x3)", "QFN 20". The build's rule, kept in step by a
# test against an index the build wrote.
_AFTER_FAMILY = "- ("


class _Unusable(ValueError):
    """A record or file this reader can't use; the message says why."""


# --- mapping -> Chip ------------------------------------------------------


def _text(data: Mapping[str, Any], key: str) -> str | None:
    """Optional text: missing, ``null`` and blank are all "not given"."""
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise _Unusable(f"{key} is not text")
    return value


def _required(data: Mapping[str, Any], key: str) -> str:
    value = _text(data, key)
    if value is None:
        raise _Unusable(f"{key} is missing")
    return value


def _texts(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _Unusable(f"{key} is not a list of text")
    return tuple(value)


def _tables(data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise _Unusable(f"{key} is not a list of tables")
    return value


def _whole_number(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if type(value) is not int:  # bool is an int; it isn't a page count
        raise _Unusable(f"{key} is not a whole number")
    return value


def chip_from_mapping(data: Mapping[str, Any]) -> Chip:
    """One chip from an index record or a parsed chip file.

    Both are the same keys; an index record has ``null`` or ``[]`` where a
    chip file leaves the key out, and either is fine here. Keys this doesn't
    know are ignored. Raises ``ValueError`` when the mapping can't be a chip.
    """
    return Chip(
        mpn=_required(data, "mpn"),
        manufacturer=_required(data, "manufacturer"),
        family=_text(data, "family"),
        package=_text(data, "package"),
        package_family=_text(data, "package_family"),
        functions=_texts(data, "functions"),
        description=_text(data, "description"),
        sources=_texts(data, "sources"),
        markings=tuple(
            Marking(
                code=_required(m, "code"),
                examples=_texts(m, "examples"),
                note=_text(m, "note"),
            )
            for m in _tables(data, "markings")
        ),
        documents=tuple(
            Document(
                kind=_required(d, "kind"),
                filename=_required(d, "filename"),
                pages=_whole_number(d, "pages"),
                sha256=_texts(d, "sha256"),
                url=_text(d, "url"),
                note=_text(d, "note"),
            )
            for d in _tables(data, "documents")
        ),
        substitutes=tuple(
            Substitute(mpn=_required(s, "mpn"), note=_text(s, "note"))
            for s in _tables(data, "substitutes")
        ),
        used_in=tuple(
            UsedIn(
                board=_required(u, "board"),
                designator=_text(u, "designator"),
                role=_text(u, "role"),
            )
            for u in _tables(data, "used_in")
        ),
    )


# --- the checkout's vocabularies -----------------------------------------


@dataclass(frozen=True)
class _Vocab:
    """What the build takes from ``vocab/``; empty when there's no ``vocab/``."""

    families: tuple[str, ...] = ()
    deprecated: Mapping[str, str] = field(default_factory=dict)

    def package_family(self, package: str | None) -> str | None:
        if not package:
            return None
        for family in sorted(self.families, key=len, reverse=True):
            if package == family:
                return family
            if package.startswith(family) and package[len(family)] in _AFTER_FAMILY:
                return family
        return None

    def current(self, functions: Iterable[str]) -> tuple[str, ...]:
        """Each slug as the build publishes it, repeats dropped, order kept."""
        return tuple(dict.fromkeys(self.deprecated.get(slug, slug) for slug in functions))


def _read_toml(path: Path) -> dict[str, Any] | None:
    """A TOML file as a dict; ``None`` (and a log line) when it can't be read."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError) as exc:  # TOMLDecodeError and bad UTF-8 are ValueErrors
        log.warning("chips: can't read %s: %s", path, exc)
        return None


def _load_vocab(root: Path) -> _Vocab:
    families: tuple[str, ...] = ()
    deprecated: dict[str, str] = {}
    packages_file = root / "vocab" / "packages.toml"
    if packages_file.is_file():
        listed = (_read_toml(packages_file) or {}).get("families")
        if isinstance(listed, list):
            families = tuple(f for f in listed if isinstance(f, str) and f)
    functions_file = root / "vocab" / "functions.toml"
    if functions_file.is_file():
        table = (_read_toml(functions_file) or {}).get("deprecated")
        if isinstance(table, Mapping):
            deprecated = {
                old: new
                for old, new in table.items()
                if isinstance(old, str) and isinstance(new, str) and new
            }
    return _Vocab(families=families, deprecated=deprecated)


# --- reading the two shapes ----------------------------------------------


def _chips_from_index(path: Path) -> list[Chip] | None:
    """Every usable chip in an index; ``None`` when the index itself isn't usable."""
    try:
        with path.open("rb") as handle:
            index = json.load(handle)
    except (OSError, ValueError, RecursionError) as exc:
        log.warning("chips: can't read %s: %s", path, exc)
        return None
    if not isinstance(index, Mapping):
        log.warning("chips: %s is not an index (no top-level object)", path)
        return None
    version = index.get("format")
    if type(version) is not int or version < 1:
        log.warning("chips: %s has no usable format number; not reading it", path)
        return None
    if version > INDEX_FORMAT:
        log.warning(
            "chips: %s is format %d and this benchhud reads format %d; not reading it",
            path,
            version,
            INDEX_FORMAT,
        )
        return None
    records = index.get("chips")
    if not isinstance(records, list):
        log.warning("chips: %s has no list of chips", path)
        return None
    chips: list[Chip] = []
    for position, record in enumerate(records):
        try:
            if not isinstance(record, Mapping):
                raise _Unusable("not a chip record")
            chips.append(chip_from_mapping(record))
        except _Unusable as exc:
            shown = record.get("mpn") if isinstance(record, Mapping) else None
            log.warning("chips: %s: skipping chip %d (%r): %s", path, position, shown, exc)
    return chips


def _chip_files(chips_dir: Path) -> list[Path]:
    """Chip files in the order chip-data's validator reads them."""
    try:
        return sorted(p for p in chips_dir.rglob("*.toml") if p.is_file())
    except OSError as exc:
        log.warning("chips: can't list %s: %s", chips_dir, exc)
        return []


def _chips_from_files(root: Path) -> list[Chip]:
    """Every usable chip file under ``root/chips``."""
    vocab = _load_vocab(root)
    chips: list[Chip] = []
    for path in _chip_files(root / "chips"):
        data = _read_toml(path)
        if data is None:
            continue
        try:
            version = data.get("schema")
            if type(version) is not int or version != CHIP_SCHEMA:
                raise _Unusable(f"schema is {version!r}; this benchhud reads {CHIP_SCHEMA}")
            chip = chip_from_mapping(data)
        except _Unusable as exc:
            log.warning("chips: skipping %s: %s", path, exc)
            continue
        chips.append(
            replace(
                chip,
                package_family=vocab.package_family(chip.package) or chip.package_family,
                functions=vocab.current(chip.functions),
            )
        )
    return chips


def _tree(root: Path) -> Iterator[Path]:
    """Everything an index is built from: ``chips/`` and the vocab files."""
    chips_dir = root / "chips"
    yield chips_dir
    yield from chips_dir.rglob("*")
    vocab_dir = root / "vocab"
    if vocab_dir.is_dir():
        yield vocab_dir
        yield from vocab_dir.glob("*.toml")


def _index_is_current(index: Path, root: Path) -> bool:
    """Whether nothing the index is built from has changed since it was written.

    Directories are checked too: deleting a chip file changes no file's time,
    only its folder's. A tree that can't be examined leaves the index standing.
    """
    try:
        built = index.stat().st_mtime
        return all(path.stat().st_mtime <= built for path in _tree(root))
    except OSError:
        return True


@dataclass(frozen=True)
class _Loaded:
    """One read of the directory. Swapped whole, never changed in place."""

    chips: tuple[Chip, ...] = ()
    by_key: Mapping[str, Chip] = field(default_factory=dict)
    origin: Path | None = None


def _first_of_each(chips: Iterable[Chip], origin: Path) -> _Loaded:
    by_key: dict[str, Chip] = {}
    for chip in chips:
        held = by_key.setdefault(chip.key, chip)
        if held is not chip:
            log.warning("chips: %s: %r is there twice; keeping the first", origin, chip.mpn.strip())
    return _Loaded(chips=tuple(by_key.values()), by_key=by_key, origin=origin)


def _load(root: Path | None, name: str) -> _Loaded:
    if root is None:
        return _Loaded()
    if root.is_file():
        chips = _chips_from_index(root)
        return _first_of_each(chips, root) if chips is not None else _Loaded()
    if not root.is_dir():
        log.warning("chips: source %r: %s is not a directory", name, root)
        return _Loaded()

    has_chip_files = (root / "chips").is_dir()
    index = next((p for p in (root / INDEX_NAME, root / "out" / INDEX_NAME) if p.is_file()), None)
    if index is not None:
        if has_chip_files and not _index_is_current(index, root):
            log.warning(
                "chips: %s is older than the chip files beside it; reading the chip files"
                " (run `chipdata build` to use the index again)",
                index,
            )
        else:
            chips = _chips_from_index(index)
            if chips is not None:
                return _first_of_each(chips, index)
    if has_chip_files:
        return _first_of_each(_chips_from_files(root), root / "chips")
    if index is None:
        log.warning("chips: source %r: no %s and no chips/ in %s", name, INDEX_NAME, root)
    return _Loaded()


# --- the source -----------------------------------------------------------


@dataclass
class DirChipSource:
    """Chips read from a chip-data checkout, a build of one, or an overlay.

    ``root`` is the directory (or an ``index.json``); ``None`` means "not
    configured" and finds nothing. See the module docstring for which files
    are read and when.
    """

    root: Path | None = None
    name: str = "chip-data"
    _loaded: _Loaded | None = field(default=None, init=False, repr=False, compare=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    @classmethod
    def from_config(cls, path: str, name: str = "chip-data") -> DirChipSource:
        """Build from config strings; a blank path means "not configured"."""
        root = Path(path).expanduser() if path.strip() else None
        return cls(root=root, name=name)

    def _current(self) -> _Loaded:
        loaded = self._loaded
        if loaded is None:
            with self._lock:
                loaded = self._loaded
                if loaded is None:
                    loaded = self._loaded = self._read()
        return loaded

    def _read(self) -> _Loaded:
        loaded = _load(self.root, self.name)
        if loaded.origin is not None:
            log.info(
                "chips: source %r: %d chips from %s", self.name, len(loaded.chips), loaded.origin
            )
        return loaded

    def reload(self) -> int:
        """Read the directory again now; returns how many chips it holds.

        Lookups made while this runs answer from the copy already held.
        """
        with self._lock:
            loaded = self._loaded = self._read()
        return len(loaded.chips)

    @property
    def origin(self) -> Path | None:
        """What the chips were read from: an index file, a ``chips`` directory,
        or ``None`` when nothing was."""
        return self._current().origin

    def get(self, mpn: str) -> Chip | None:
        wanted = mpn_key(mpn)
        if not wanted:
            return None
        return self._current().by_key.get(wanted)

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return match_markings(self._current().chips, text)
