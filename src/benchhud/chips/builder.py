"""From the ``[chips]`` config to the chip source the HUD looks things up in.

The config holds an ordered list of sources; the order is precedence. This
turns it into one :class:`~benchhud.chips.source.ChipSource`:

- **No usable source → the null source.** Nothing is read, nothing is
  started, and the HUD is exactly what it was before chips existed.
- **One or more → a composite**, even for one, so "where did this value come
  from" has the same answer shape however many there are.

An entry is left out — never an error — when it's switched off, when its
``kind`` isn't one this benchhud reads (``dir`` is the only kind so far), when
it has no folder, or when its name was already taken by an entry above it
(the name is what a value's provenance is recorded under, so it has to mean
one source). Each of those but "switched off" writes a log line.

Building reads nothing. Reading a directory is the slow part, and it belongs
off the GUI thread: :func:`warm` hands the read to a worker, so the first
lookup finds everything already in memory.

The datasheet library is built the same way from ``[chips] datasheet_dir``:
:func:`build_datasheet_library` reads nothing, :func:`warm_library` lists the
folder on a worker, and no folder means no library and no thread.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from typing import Protocol

from benchhud.chips.composite import CompositeChipSource
from benchhud.chips.datasheets import DatasheetLibrary
from benchhud.chips.dir_source import DirChipSource, LoadReport
from benchhud.chips.source import ChipSource, NullChipSource

log = logging.getLogger(__name__)

#: The ``kind`` values this benchhud can build a source for.
KIND_DIR = "dir"
KNOWN_KINDS = (KIND_DIR,)

#: Runs a job off the GUI thread.
Executor = Callable[[Callable[[], None]], None]


class SourceEntry(Protocol):
    """One ``[[chips.sources]]`` entry, as the config holds it."""

    name: str
    kind: str
    path: str
    enabled: bool


def name_key(name: str) -> str:
    """A source name as it's compared: two names differing only in case clash."""
    return " ".join(name.split()).casefold()


def left_out(entry: SourceEntry, taken: Iterable[str] = ()) -> str | None:
    """Why this entry doesn't become a source — ``None`` when it does.

    ``taken`` is the :func:`name_key` of every entry above it that did. The
    reason is a short phrase fit for a log line or a status column.
    """
    if not entry.enabled:
        return "switched off"
    if entry.kind not in KNOWN_KINDS:
        return f'kind "{entry.kind}" isn\'t one this benchhud reads'
    if not entry.path.strip():
        return "no folder set"
    if name_key(entry.name) in set(taken):
        return f'the name "{entry.name}" is already used above'
    return None


def build_chip_source(entries: Iterable[SourceEntry]) -> ChipSource:
    """The source for these config entries, in their order. Reads nothing."""
    sources: list[ChipSource] = []
    taken: set[str] = set()
    for entry in entries:
        reason = left_out(entry, taken)
        if reason is None:
            taken.add(name_key(entry.name))
            sources.append(DirChipSource.from_config(entry.path.strip(), entry.name))
        elif entry.enabled:
            log.warning("chips: leaving out source %r: %s", entry.name, reason)
    return CompositeChipSource(sources) if sources else NullChipSource()


def _thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="chips-load", daemon=True).start()


def warm(source: ChipSource, run: Executor | None = None) -> bool:
    """Have ``source`` read its directories now, off the calling thread.

    ``run`` is what takes the job; left out, it's a daemon thread. Returns
    whether there was anything to read — the null source has nothing, and no
    thread is started for it.
    """
    reload = getattr(source, "reload", None)
    if not callable(reload):
        return False
    (run or _thread)(reload)
    return True


def build_datasheet_library(path: str) -> DatasheetLibrary:
    """The datasheet library for ``[chips] datasheet_dir``. Reads nothing.

    A blank folder is a library that isn't configured: it finds nothing and
    never touches the disk.
    """
    return DatasheetLibrary.from_config(path.strip())


def warm_library(library: DatasheetLibrary, run: Executor | None = None) -> bool:
    """Have ``library`` list its folder now, off the calling thread.

    Listing only — a file is hashed when a lookup needs it. Returns whether
    there was a folder to list; with none, no thread is started.
    """
    if not library.configured:
        return False
    (run or _thread)(library.reload)
    return True


def probe_folder(path: str) -> LoadReport:
    """What reading ``path`` as a chip source comes to, without keeping it.

    For a screen that shows what a folder holds before it's saved. It reads
    the directory, so call it off the GUI thread.
    """
    return DirChipSource.from_config(path.strip(), "probe").report()
