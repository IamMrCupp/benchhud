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
  nothing to say so. "Older" goes by modification time, of chip and vocab
  files and of the folders holding them — deleting a file changes only its
  folder's. It errs towards the chip files: anything that touches a folder
  makes the index look old, and the chip files are never the wrong answer.
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
know — is skipped with one log line and the rest still load. So is a file or
a folder it isn't allowed to read. ``chipdata validate`` is the check; this
never raises.

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
held, never a half-read one. Only the very first read has no copy to answer
from, so lookups that arrive during it wait for it.

The first lookup does the reading, so the caller decides which thread pays for
it — call ``reload()`` from a worker before the first lookup to keep it off
the GUI thread. Loading is guarded by a lock, so that is safe.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import tomllib
from bisect import bisect_left
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from benchhud.chips.marking import MarkingMatch, match_markings
from benchhud.chips.model import Chip, Document, Marking, Substitute, UsedIn, mpn_key
from benchhud.chips.source import mpn_prefix_order

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


#: Where a load came from, for :class:`LoadReport`.
FROM_INDEX = "index"
FROM_FILES = "files"

# How many problems one load remembers for its report. The log has them all.
_MOST_PROBLEMS = 100

# The problems the load running in this thread has hit so far.
_problems: ContextVar[list[str] | None] = ContextVar("benchhud_chips_load_problems", default=None)


def _warn(message: str, *args: object, source: str | None = None) -> None:
    """Log a problem, and keep it for the report of the load that hit it.

    With ``source`` the log line names the source. The kept line doesn't: a
    report is already about one source, so it starts at what went wrong.
    """
    if source is None:
        log.warning("chips: " + message, *args)
    else:
        log.warning("chips: source %r: " + message, source, *args)
    held = _problems.get()
    if held is not None:
        held.append(message % args)


@dataclass(frozen=True)
class LoadReport:
    """What one read of a directory came to — for a screen that says so.

    ``read_from`` is :data:`FROM_INDEX`, :data:`FROM_FILES`, or ``""`` when
    nothing was read; ``origin`` is the index file or the ``chips`` folder.
    ``problems`` is every line the read logged, in order: why nothing loaded,
    or what was skipped along the way. A long run of them is cut short.
    """

    chips: int = 0
    read_from: str = ""
    origin: Path | None = None
    problems: tuple[str, ...] = ()


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


def _is_file(path: Path) -> bool:
    """``path.is_file()`` that can't raise.

    Before Python 3.14 pathlib lets a permission error or an over-long name
    out as an exception; here either just means "no file this can read".
    """
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    """``path.is_dir()`` that can't raise — see :func:`_is_file`."""
    try:
        return path.is_dir()
    except OSError:
        return False


def _read_toml(path: Path, *, missing_ok: bool = False) -> dict[str, Any] | None:
    """A TOML file as a dict; ``None`` (and a log line) when it can't be read.

    With ``missing_ok`` a file that isn't there is ``None`` without the line.
    """
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        if not missing_ok:
            _warn("can't read %s: %s", path, exc)
        return None
    # TOMLDecodeError and bad UTF-8 are ValueErrors; nesting a few hundred
    # levels deep runs the parser out of stack.
    except (OSError, ValueError, RecursionError) as exc:
        _warn("can't read %s: %s", path, exc)
        return None


def _load_vocab(root: Path) -> _Vocab:
    families: tuple[str, ...] = ()
    deprecated: dict[str, str] = {}
    packages = _read_toml(root / "vocab" / "packages.toml", missing_ok=True) or {}
    listed = packages.get("families")
    if isinstance(listed, list):
        families = tuple(f for f in listed if isinstance(f, str) and f)
    functions = _read_toml(root / "vocab" / "functions.toml", missing_ok=True) or {}
    table = functions.get("deprecated")
    if isinstance(table, Mapping):
        deprecated = {
            old: new
            for old, new in table.items()
            if isinstance(old, str) and isinstance(new, str) and new
        }
    return _Vocab(families=families, deprecated=deprecated)


def _walk(top: Path, *, say: bool) -> tuple[list[Path], list[Path]]:
    """The folders (``top`` first) and the files under ``top``.

    A folder that can't be listed is passed over — with a log line when
    ``say`` — and the rest are still walked. Folders that are symlinks aren't
    followed, so a link back up the tree can't loop.
    """

    def cant_list(exc: OSError) -> None:
        if say:
            _warn("can't list %s: %s", exc.filename or top, exc)

    folders, files = [top], []
    for here, folder_names, file_names in os.walk(top, onerror=cant_list):
        folders.extend(Path(here, name) for name in folder_names)
        files.extend(Path(here, name) for name in file_names)
    return folders, files


def _toml_files(files: Iterable[Path]) -> list[Path]:
    """The ``.toml`` files, in the order chip-data's validator reads them."""
    return sorted(path for path in files if path.name.endswith(".toml"))


# --- reading the two shapes ----------------------------------------------


def _chips_from_index(path: Path) -> list[Chip] | None:
    """Every usable chip in an index; ``None`` when the index itself isn't usable."""
    try:
        with path.open("rb") as handle:
            index = json.load(handle)
    except (OSError, ValueError, RecursionError) as exc:
        _warn("can't read %s: %s", path, exc)
        return None
    if not isinstance(index, Mapping):
        _warn("%s is not an index (no top-level object)", path)
        return None
    version = index.get("format")
    if type(version) is not int or version < 1:
        _warn("%s has no usable format number; not reading it", path)
        return None
    if version > INDEX_FORMAT:
        _warn(
            "%s is format %d and this benchhud reads format %d; not reading it",
            path,
            version,
            INDEX_FORMAT,
        )
        return None
    records = index.get("chips")
    if not isinstance(records, list):
        _warn("%s has no list of chips", path)
        return None
    chips: list[Chip] = []
    for position, record in enumerate(records):
        try:
            if not isinstance(record, Mapping):
                raise _Unusable("not a chip record")
            chips.append(chip_from_mapping(record))
        except _Unusable as exc:
            shown = record.get("mpn") if isinstance(record, Mapping) else None
            _warn("%s: skipping chip %d (%r): %s", path, position, shown, exc)
    return chips


def _chips_from_files(root: Path) -> list[Chip]:
    """Every usable chip file under ``root/chips``."""
    vocab = _load_vocab(root)
    chips: list[Chip] = []
    for path in _toml_files(_walk(root / "chips", say=True)[1]):
        data = _read_toml(path)
        if data is None:
            continue
        try:
            version = data.get("schema")
            if type(version) is not int or version != CHIP_SCHEMA:
                raise _Unusable(f"schema is {version!r}; this benchhud reads {CHIP_SCHEMA}")
            chip = chip_from_mapping(data)
        except _Unusable as exc:
            _warn("skipping %s: %s", path, exc)
            continue
        # What the build works out, worked out the same way. A chip file has
        # no package_family key of its own, so nothing is taken from one.
        chips.append(
            replace(
                chip,
                package_family=vocab.package_family(chip.package),
                functions=vocab.current(chip.functions),
            )
        )
    return chips


def _index_is_current(index: Path, root: Path) -> bool:
    """Whether nothing the index is built from has changed since it was written.

    Looks at the chip files, the vocab files and the folders holding them:
    deleting a file changes no file's time, only its folder's. A path that
    can't be examined is passed over, not taken as an answer either way.
    """
    try:
        built = index.stat().st_mtime
    except OSError:
        return True  # reading it is about to fail, and that says why
    watched: list[Path] = []
    for top in (root / "chips", root / "vocab"):
        folders, files = _walk(top, say=False)
        watched += folders
        watched += _toml_files(files)
    for path in watched:
        try:
            if path.stat().st_mtime > built:
                return False
        except OSError:
            continue
    return True


@dataclass(frozen=True)
class _Loaded:
    """One read of the directory. Swapped whole, never changed in place."""

    chips: tuple[Chip, ...] = ()
    by_key: Mapping[str, Chip] = field(default_factory=dict)
    #: Every key in ``by_key``, sorted: a part-number search bisects it.
    keys: tuple[str, ...] = ()
    origin: Path | None = None
    read_from: str = ""
    problems: tuple[str, ...] = ()


def _first_of_each(chips: Iterable[Chip], origin: Path, read_from: str) -> _Loaded:
    by_key: dict[str, Chip] = {}
    for chip in chips:
        held = by_key.setdefault(chip.key, chip)
        if held is not chip:
            _warn("%s: %r is there twice; keeping the first", origin, chip.mpn.strip())
    return _Loaded(
        chips=tuple(by_key.values()),
        by_key=by_key,
        keys=tuple(sorted(by_key)),
        origin=origin,
        read_from=read_from,
    )


def _load(root: Path | None, name: str) -> _Loaded:
    if root is None:
        return _Loaded()
    if _is_file(root):
        chips = _chips_from_index(root)
        return _first_of_each(chips, root, FROM_INDEX) if chips is not None else _Loaded()
    if not _is_dir(root):
        _warn("%s is not a directory this can read", root, source=name)
        return _Loaded()

    has_chip_files = _is_dir(root / "chips")
    found_an_index = False
    for index in (root / INDEX_NAME, root / "out" / INDEX_NAME):
        if not _is_file(index):
            continue
        found_an_index = True
        if has_chip_files and not _index_is_current(index, root):
            _warn(
                "%s may be out of date: something under chips/ or vocab/ changed after"
                " it was written. Reading the chip files (run `chipdata build` to use the"
                " index again)",
                index,
            )
            continue
        chips = _chips_from_index(index)
        if chips is not None:
            return _first_of_each(chips, index, FROM_INDEX)
    if has_chip_files:
        return _first_of_each(_chips_from_files(root), root / "chips", FROM_FILES)
    if not found_an_index:
        _warn("no %s and no chips/ in %s", INDEX_NAME, root, source=name)
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
        if not path.strip():
            return cls(root=None, name=name)
        try:
            root = Path(path).expanduser()
        except RuntimeError:  # "~nobody/chips": no such user, so no such directory
            root = Path(path)
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
        problems: list[str] = []
        collecting = _problems.set(problems)
        try:
            loaded = _load(self.root, self.name)
        except Exception as exc:
            # The seam's rule: a source that can't answer gives nothing. Every
            # failure this knows about is handled where it happens; this is for
            # the one it doesn't, so a lookup can't raise into the HUD.
            log.exception("chips: source %r: reading %s failed", self.name, self.root)
            problems.append(f"reading {self.root} failed: {exc}")
            loaded = _Loaded()
        finally:
            _problems.reset(collecting)
        if loaded.origin is not None:
            log.info(
                "chips: source %r: %d chips from %s", self.name, len(loaded.chips), loaded.origin
            )
        if len(problems) > _MOST_PROBLEMS:
            more = len(problems) - _MOST_PROBLEMS
            problems[_MOST_PROBLEMS:] = [f"… and {more} more; the log has them all"]
        return replace(loaded, problems=tuple(problems))

    def reload(self) -> int:
        """Read the directory again now; returns how many chips it holds.

        Lookups made while this runs answer from the copy already held; two
        reloads run one after the other, so the later read is the one kept.
        """
        with self._lock:
            loaded = self._loaded = self._read()
        return len(loaded.chips)

    @property
    def origin(self) -> Path | None:
        """What the chips were read from: an index file, a ``chips`` directory,
        or ``None`` when nothing was."""
        return self._current().origin

    def report(self) -> LoadReport:
        """What the read came to: how many chips, from what, and what went wrong.

        Like a lookup, it reads the directory if nothing has yet.
        """
        loaded = self._current()
        return LoadReport(
            chips=len(loaded.chips),
            read_from=loaded.read_from,
            origin=loaded.origin,
            problems=loaded.problems,
        )

    def get(self, mpn: str) -> Chip | None:
        wanted = mpn_key(mpn)
        if not wanted:
            return None
        return self._current().by_key.get(wanted)

    def by_marking(self, text: str) -> list[MarkingMatch]:
        return match_markings(self._current().chips, text)

    def by_mpn_prefix(self, text: str) -> list[Chip]:
        wanted = mpn_key(text)
        if not wanted:
            return []
        loaded = self._current()
        # Every key that starts with the text sorts in one run, from where the
        # text itself would go — so a search costs the run, not the checkout.
        keys = loaded.keys
        found: list[Chip] = []
        for at in range(bisect_left(keys, wanted), len(keys)):
            if not keys[at].startswith(wanted):
                break
            found.append(loaded.by_key[keys[at]])
        return sorted(found, key=mpn_prefix_order)
