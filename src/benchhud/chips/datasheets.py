"""The datasheet library — "do I hold this document?".

A chip carries a *fingerprint* of each of its documents (:class:`Document`: a
filename, a page count, a list of SHA-256 hashes) and never the document. The
documents themselves are files you keep in a folder, the way board views are
kept in ``~/boards``. This finds the file for a fingerprint.

**By hash first, by name second.** A file whose SHA-256 is one of the
document's hashes *is* that document, whatever it's called. With no hash match,
a file with the same name stem is taken instead (``EX1234.pdf`` for a document
called ``ex1234.PDF``). The same revision saved by two sites can differ byte
for byte, so the hash is a helper and not a gate. A name match is the weaker
claim — it may be another revision — so every result says which it was.

**When the folder is read.** Listed on the first lookup and kept; ``reload()``
lists it again, and nothing watches the folder. Hashing a folder of PDFs isn't
free, so it's put off and narrowed: a lookup hashes the files that share the
document's name first, and only when none of those is the document does it
hash the rest. A file's hash is kept with its size and modification time, and
worked out again only when one of those changes — across ``reload()`` too. The
hashes live in memory, not on disk, so a restart hashes again, on demand. A
first lookup can take a moment on a big folder: call it off the GUI thread.

Read-only, and nothing raises. No folder, a folder that's gone, a file it may
not read: that resolves to nothing, with one log line where it helps. Nothing
here downloads a document or touches the network; a document's ``url`` is not
fetched. No Qt, so it tests against a ``tmp_path``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from benchhud.boards.opener import OsOpener, default_os_open
from benchhud.chips.model import Chip, Document

log = logging.getLogger(__name__)

#: The document kind an "Open datasheet" action looks for.
DATASHEET_KIND = "datasheet"

_CHUNK = 1 << 20


class MatchedBy(StrEnum):
    """How sure a found file is."""

    #: The file's SHA-256 is one of the document's: it is that document.
    HASH = "hash"
    #: Same name stem, different (or no) hash: likely it, maybe another revision.
    NAME = "name"


@dataclass(frozen=True)
class DatasheetFile:
    """A file in the library, and what makes it the document asked for."""

    path: Path
    matched_by: MatchedBy

    @property
    def exact(self) -> bool:
        """Whether this is the fingerprinted revision, not just the same name."""
        return self.matched_by is MatchedBy.HASH


@dataclass(frozen=True)
class ChipDocument:
    """One of a chip's documents, with the file held for it (or ``None``)."""

    document: Document
    file: DatasheetFile | None = None


def name_key(filename: str | None) -> str:
    """The form of a filename two documents are compared by: its stem, case ignored.

    ``EX1234.pdf``, ``ex1234.PDF`` and ``docs/EX1234.pdf`` are one name. Blank
    or missing gives ``""``, which matches nothing.
    """
    text = (filename or "").strip()
    if not text:
        return ""
    # A fingerprint written on Windows can carry backslashes; take the last part.
    return Path(text.replace("\\", "/")).stem.casefold()


def _hash_keys(hashes: Iterable[str]) -> frozenset[str]:
    return frozenset(h.strip().lower() for h in hashes if isinstance(h, str) and h.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _list(root: Path) -> tuple[Path, ...]:
    """Every file under ``root``, in a fixed order; hidden ones left out.

    Folders that are symlinks aren't followed, so a link back up the tree can't
    loop. A folder that can't be listed is passed over with a log line.
    """

    def cant_list(exc: OSError) -> None:
        log.warning("datasheets: can't list %s: %s", exc.filename or root, exc)

    found: list[Path] = []
    for here, folder_names, file_names in os.walk(root, onerror=cant_list):
        folder_names[:] = sorted(name for name in folder_names if not name.startswith("."))
        found.extend(Path(here, name) for name in sorted(file_names) if not name.startswith("."))
    return tuple(found)


@dataclass
class DatasheetLibrary:
    """A folder of documents, searched by fingerprint.

    ``root`` is the folder; ``None`` means "not configured" and finds nothing.
    See the module docstring for when it is read and hashed.
    """

    root: Path | None = None
    _files: tuple[Path, ...] | None = field(default=None, init=False, repr=False, compare=False)
    #: path -> (size, mtime in ns, sha256 or None when it couldn't be read).
    _hashes: dict[Path, tuple[int, int, str | None]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )

    @classmethod
    def from_config(cls, path: str) -> DatasheetLibrary:
        """Build from a config string; a blank path means "not configured"."""
        if not path.strip():
            return cls(root=None)
        try:
            return cls(root=Path(path).expanduser())
        except RuntimeError:  # "~nobody/datasheets": no such user, so no such folder
            return cls(root=Path(path))

    @property
    def configured(self) -> bool:
        """Whether a folder was named at all (it may still not exist)."""
        return self.root is not None

    def files(self) -> tuple[Path, ...]:
        """The files in the library. Listed once and kept; see :meth:`reload`."""
        files = self._files
        if files is None:
            with self._lock:
                files = self._files
                if files is None:
                    files = self._files = self._read()
        return files

    def _read(self) -> tuple[Path, ...]:
        root = self.root
        if root is None:
            return ()
        try:
            if not root.is_dir():
                log.warning("datasheets: %s is not a folder this can read", root)
                return ()
            return _list(root)
        except Exception:
            # Nothing here may raise into the HUD; this is for the failure the
            # handled cases above didn't foresee.
            log.exception("datasheets: listing %s failed", root)
            return ()

    def reload(self) -> int:
        """List the folder again now; returns how many files it holds.

        Hashes already worked out are kept for files that haven't changed.
        """
        with self._lock:
            files = self._files = self._read()
            held = set(files)
            for gone in [path for path in self._hashes if path not in held]:
                del self._hashes[gone]
        return len(files)

    def _hash_of(self, path: Path) -> str | None:
        """The file's SHA-256, from the cache while its size and time hold."""
        try:
            stat = path.stat()
            stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError as exc:
            log.warning("datasheets: can't read %s: %s", path, exc)
            return None
        with self._lock:
            held = self._hashes.get(path)
        if held is not None and held[:2] == stamp:
            return held[2]
        try:
            digest: str | None = _sha256(path)
        except OSError as exc:
            # Remembered as unreadable, so it's said once and not on every lookup.
            log.warning("datasheets: can't read %s: %s", path, exc)
            digest = None
        with self._lock:
            self._hashes[path] = (*stamp, digest)
        return digest

    def find(self, document: Document) -> DatasheetFile | None:
        """The file held for ``document``: by hash, else by name, else ``None``."""
        try:
            return self._find(document)
        except Exception:
            log.exception("datasheets: looking for %r failed", getattr(document, "filename", None))
            return None

    def _find(self, document: Document) -> DatasheetFile | None:
        files = self.files()
        if not files:
            return None
        wanted_name = name_key(document.filename)
        same_name = [p for p in files if wanted_name and name_key(p.name) == wanted_name]
        wanted_hashes = _hash_keys(document.sha256)
        if wanted_hashes:
            # The files with the document's own name first: that's where it
            # usually is, and it saves hashing the whole folder to find out.
            named = set(same_name)
            for path in (*same_name, *(p for p in files if p not in named)):
                if self._hash_of(path) in wanted_hashes:
                    return DatasheetFile(path, MatchedBy.HASH)
        if not same_name:
            return None
        # Several files with one stem: the one with the document's own
        # extension, else the first in listing order.
        suffix = Path((document.filename or "").strip().replace("\\", "/")).suffix.casefold()
        same_name.sort(key=lambda p: p.suffix.casefold() != suffix)
        return DatasheetFile(same_name[0], MatchedBy.NAME)

    def for_chip(self, chip: Chip) -> tuple[ChipDocument, ...]:
        """Each of the chip's documents, in its own order, with the file found."""
        return tuple(ChipDocument(document, self.find(document)) for document in chip.documents)

    def datasheet(self, chip: Chip) -> DatasheetFile | None:
        """The chip's datasheet, if one is held: what "Open datasheet" opens.

        The first ``datasheet`` document with a file; an exact (hash) match is
        taken over a name match that comes earlier in the chip's list.
        """
        held = [
            entry.file
            for entry in self.for_chip(chip)
            if entry.document.kind == DATASHEET_KIND and entry.file is not None
        ]
        return next((f for f in held if f.exact), held[0] if held else None)


def open_document(path: Path, *, os_open: OsOpener | None = None) -> bool:
    """Open a held document with the OS handler. Never raises into the HUD."""
    try:
        if not path.exists():
            log.warning("datasheets: %s is gone", path)
            return False
        (os_open or default_os_open)(path)
    except Exception:
        log.exception("datasheets: could not open %s", path)
        return False
    return True
