"""Opening the document held for a chip — and the words for how it was found.

Two places open a chip's datasheet: the inventory row and the Find chip
dialog. Both have to tell the operator the same thing in the same words,
because one of those things matters: **a file found by its hash is the
document; a file found by its name only may be another revision.** So the
finding, the opening and the wording live here, once.

Only the chip's own name for a document is ever said — the last part of it,
cut short if it's long (:func:`document_name`). Never the name or the folder
of the file on this machine: a file found by its hash can be called anything
and filed anywhere, and what's said here can end up on a panel that's on
stream.

No Qt. :func:`open_held` finds and may hash, so it's for a worker thread; the
rest are plain functions of what was found.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from benchhud.chips.datasheets import (
    DATASHEET_KIND,
    ChipDocument,
    DatasheetLibrary,
    open_document,
)
from benchhud.chips.model import Chip, Document

#: A document name longer than this is cut in the middle to show.
NAME_LIMIT = 40

#: Opens a held file with whatever the OS uses; false when it couldn't.
FileOpener = Callable[[Path], bool]


def document_name(document: Document) -> str:
    """The name a chip gives a document, fit to show; ``""`` when it gives none.

    Only the last part: a fingerprint can carry the folders of whoever wrote
    it down (``C:\\Users\\…\\EX1234.pdf``), and those are nobody's business
    on a panel that can be on stream. A long name is cut in the middle, where
    it matters least.
    """
    name = (document.filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1].strip()
    if len(name) > NAME_LIMIT:
        keep = (NAME_LIMIT - 1) // 2
        name = f"{name[:keep]}…{name[-(NAME_LIMIT - 1 - keep) :]}"
    return name


def find_datasheet(library: DatasheetLibrary, chip: Chip) -> ChipDocument | None:
    """The chip's datasheet and the file held for it, or ``None`` when none is.

    The same pick as :meth:`DatasheetLibrary.datasheet` — the first datasheet
    with a file, an exact match over an earlier name match — but it keeps the
    document, so the chip's name for it can be shown and not the file's.
    This is the call that hashes: off the GUI thread only.
    """
    held = [
        entry
        for entry in library.for_chip(chip)
        if entry.document.kind == DATASHEET_KIND and entry.file is not None
    ]
    return next((entry for entry in held if entry.file.exact), held[0] if held else None)


def held_line(held: ChipDocument) -> str:
    """What's held for a document, before it's opened — exact copy or name only."""
    name = document_name(held.document) or "the datasheet listed"
    if held.file is not None and held.file.exact:
        return f"{name} — held, an exact copy"
    return f"{name} — held, matched by name only; it may be another revision"


def not_held_line(
    chip: Chip, document: Document | None = None, *, library_configured: bool = True
) -> str:
    """Why there's nothing to open. ``document`` is ``None`` for "its datasheet"."""
    if document is None:
        listed = [d for d in chip.documents if d.kind == DATASHEET_KIND]
        if not listed:
            return "No datasheet is listed for this chip"
        document = listed[0]
    name = document_name(document) or "one with no name"
    if not library_configured:
        return f"No datasheet folder set — the chip lists {name}"
    return f"Not held — the chip lists {name}"


def opened_line(held: ChipDocument, opened: bool) -> str:
    """What became of opening a held document — said again if it was a name match."""
    name = document_name(held.document) or "the datasheet"
    if not opened:
        return f"Couldn't open {name}."
    if held.file is not None and held.file.exact:
        return f"Opened {name}."
    return f"Opened {name} — matched by name only, so check it's the right revision."


@dataclass(frozen=True)
class Opened:
    """What :func:`open_held` did: whether a file was opened, and what to say."""

    opened: bool
    detail: str


def open_held(
    library: DatasheetLibrary,
    chip: Chip,
    document: Document | None = None,
    *,
    open_file: FileOpener = open_document,
) -> Opened:
    """Find the file held for a chip's document and open it.

    ``document`` is ``None`` for "this chip's datasheet". The answer says
    which it was — an exact copy or a name match — in the words above. It
    can hash a folder: call it off the GUI thread.
    """
    if document is None:
        held = find_datasheet(library, chip)
    else:
        held = ChipDocument(document, library.find(document))
    if held is None or held.file is None:
        line = not_held_line(chip, document, library_configured=library.configured)
        return Opened(False, f"{line}.")
    opened = bool(open_file(held.file.path))
    return Opened(opened, opened_line(held, opened))
