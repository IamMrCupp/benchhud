"""The local board-documentation library — "do I have this board?".

Board views live on the repair box as files named after the board's part number
(``820-00239.obv``, ``.brd``, ``.bdv``), in a directory the operator configures.
This resolves a board id to the local file(s), searches the library, and reports
what's missing — the input to a "boards to source" worklist.

benchhud does not render board views; it locates them and hands them to
OpenBoardView (see :mod:`benchhud.boards.opener`).

Everything here is pure filesystem work with no Qt, so it tests against a
``tmp_path``. An unconfigured or missing library is not an error — it just
resolves to nothing, so the HUD works fine on a bench with no library yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Board-view formats OpenBoardView reads (per the intake handoff).
DEFAULT_EXTENSIONS: tuple[str, ...] = ("obv", "brd", "bdv")

#: Schematic formats stored next to the board file. FlexBV auto-loads a PDF that
#: shares the board file's name, so co-locating them is all the pairing needs.
DEFAULT_SCHEMATIC_EXTENSIONS: tuple[str, ...] = ("pdf",)


def _normalise_exts(extensions: list[str] | None) -> tuple[str, ...]:
    """Lowercase, strip dots, drop blanks — ``[".PDF", "brd"]`` → ``("pdf", "brd")``."""
    return tuple(e.strip().lstrip(".").lower() for e in (extensions or []) if e.strip())


def _match(files: list[BoardFile], board_id: str | None) -> list[BoardFile]:
    """Files whose board id equals ``board_id`` (case-insensitive)."""
    if not board_id or not board_id.strip():
        return []
    wanted = board_id.strip().lower()
    return [f for f in files if f.board_id.lower() == wanted]


@dataclass(frozen=True)
class BoardFile:
    """One board-view file in the library."""

    board_id: str
    path: Path

    @property
    def format(self) -> str:
        """The file's extension without the dot (``obv``, ``brd``, …)."""
        return self.path.suffix.lstrip(".").lower()


@dataclass
class BoardLibrary:
    """A directory of board-view files (and their schematics) keyed by board id."""

    root: Path | None = None
    extensions: tuple[str, ...] = field(default=DEFAULT_EXTENSIONS)
    schematic_extensions: tuple[str, ...] = field(default=DEFAULT_SCHEMATIC_EXTENSIONS)

    @classmethod
    def from_config(
        cls,
        library_dir: str,
        extensions: list[str] | None = None,
        schematic_extensions: list[str] | None = None,
    ) -> BoardLibrary:
        """Build from config strings; a blank directory means "not configured"."""
        root = Path(library_dir).expanduser() if library_dir.strip() else None
        exts = _normalise_exts(extensions) or DEFAULT_EXTENSIONS
        schem = _normalise_exts(schematic_extensions) or DEFAULT_SCHEMATIC_EXTENSIONS
        return cls(root=root, extensions=exts, schematic_extensions=schem)

    @property
    def configured(self) -> bool:
        """True when a library directory is set and actually exists."""
        return self.root is not None and self.root.is_dir()

    def _scan(self, extensions: tuple[str, ...]) -> list[BoardFile]:
        """Every file with one of ``extensions``, sorted by board id then format.

        Scans recursively — real libraries get filed into sub-folders by vendor
        or model, and the board id is the filename either way.
        """
        if not self.configured:
            return []
        assert self.root is not None  # narrowed by `configured`
        found = [
            BoardFile(board_id=path.stem, path=path)
            for path in self.root.rglob("*")
            if path.is_file() and path.suffix.lstrip(".").lower() in extensions
        ]
        return sorted(found, key=lambda b: (b.board_id.lower(), b.format))

    def list_all(self) -> list[BoardFile]:
        """Every board-view file in the library (schematics excluded)."""
        return self._scan(self.extensions)

    def resolve(self, board_id: str | None) -> list[BoardFile]:
        """Board-view files for exactly this board id (case-insensitive)."""
        return _match(self.list_all(), board_id)

    def has(self, board_id: str | None) -> bool:
        """Whether the library holds any board-view file for ``board_id``."""
        return bool(self.resolve(board_id))

    def resolve_schematic(self, board_id: str | None) -> list[BoardFile]:
        """Schematic file(s) (e.g. the PDF) for this board id, if any."""
        return _match(self._scan(self.schematic_extensions), board_id)

    def has_schematic(self, board_id: str | None) -> bool:
        """Whether a schematic is filed alongside this board."""
        return bool(self.resolve_schematic(board_id))

    def search(self, query: str) -> list[BoardFile]:
        """Board files whose id contains ``query`` (case-insensitive).

        A blank query lists everything — the natural "show me what I have".
        """
        needle = query.strip().lower()
        if not needle:
            return self.list_all()
        return [b for b in self.list_all() if needle in b.board_id.lower()]

    def missing(self, board_ids: list[str | None]) -> list[str]:
        """Which of ``board_ids`` have no local file — the "to source" list.

        De-duplicated, blanks dropped, original order preserved.
        """
        seen: set[str] = set()
        out: list[str] = []
        for board_id in board_ids:
            if not board_id or not board_id.strip():
                continue
            key = board_id.strip()
            if key.lower() in seen:
                continue
            seen.add(key.lower())
            if not self.has(key):
                out.append(key)
        return out
