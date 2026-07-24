"""The "boards to source" worklist.

Open tickets whose ``board_id`` has no file in the local library — the boards to
go find and download. Mirrors a parts "to order" list: it turns "do I have it?"
into a work queue.

Pure: takes tickets + a library, returns the gaps. Deliberately carries only
``ref``, ``device`` and ``board_id`` — **never ``customer``**, so this list is
safe to show anywhere, including a composited surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from benchhud.boards.library import BoardLibrary
from benchhud.tickets.source import Ticket


@dataclass(frozen=True)
class BoardNeed:
    """A board that open tickets need but the library doesn't have."""

    board_id: str
    refs: tuple[str, ...]  # the open tickets waiting on it
    devices: tuple[str, ...]  # what those tickets are, for context

    @property
    def summary(self) -> str:
        """``820-00239 — 2 tickets (CDJ-3000)``."""
        plural = "ticket" if len(self.refs) == 1 else "tickets"
        devices = ", ".join(self.devices)
        return f"{self.board_id} — {len(self.refs)} {plural} ({devices})"


def boards_to_source(tickets: Sequence[Ticket], library: BoardLibrary) -> list[BoardNeed]:
    """Boards referenced by ``tickets`` that aren't in ``library``.

    Grouped by board id so one board needed by three tickets is one line of
    work, not three. Tickets with no ``board_id`` are skipped — nothing to
    source when we don't know the board.
    """
    by_board: dict[str, list[Ticket]] = {}
    for ticket in tickets:
        board_id = (ticket.board_id or "").strip()
        if board_id:
            by_board.setdefault(board_id, []).append(ticket)

    return [
        BoardNeed(
            board_id=board_id,
            refs=tuple(t.ref for t in by_board[board_id]),
            # dict.fromkeys de-dupes while keeping first-seen order.
            devices=tuple(dict.fromkeys(t.device for t in by_board[board_id] if t.device)),
        )
        for board_id in library.missing(list(by_board))
    ]
