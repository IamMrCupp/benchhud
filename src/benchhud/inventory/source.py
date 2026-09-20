"""Pluggable inventory sources.

benchhud is the *bench-side interface* to the shop's parts inventory, not the
inventory itself. Stock lives in bench-parts — a separate self-hosted service —
and benchhud reads levels from it and records consumption against the active
job. The seam mirrors :mod:`benchhud.tickets.source`: a Protocol, a Null
default, and an in-memory fake, so the HTTP adapter is just the first backend
and the HUD works entirely standalone without one.

Unlike tickets, parts data carries **no PII** — every field on :class:`Part`
is safe to composite into the stream surface.

The one behavioural difference from the ticket seam: ``consume`` is a *write*
into the shop's movements ledger. Reads degrade to empty like ticket fetches
do, but a consume that didn't record must never look like one that did — it
raises :class:`InventoryWriteError` instead, and callers surface that to the
operator. Consume is not idempotent, so no layer here retries it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable


class InventoryWriteError(Exception):
    """A stock write did not record.

    Carries a human-readable reason (bench-parts sends one: "insufficient
    stock", "part is retired", …). Raised rather than logged-and-swallowed
    because a silent ledger miss is worse than a visible failure at the bench.
    """


@dataclass(frozen=True)
class Part:
    """A part as bench-parts reports it.

    ``status`` is derived *server-side* (``out`` / ``low`` / ``ok``) with the
    same rule the shop tablet's colours use — display it, don't re-derive it,
    so the HUD and the shelf always agree. ``unit_cost_cents`` is an integer
    of cents (``None`` when uncosted). ``url`` links back to the part's page
    in the bench-parts web UI.
    """

    id: int
    description: str
    status: str = "ok"
    quantity_on_hand: int = 0
    min_quantity: int = 0
    mpn: str | None = None
    category_label: str | None = None
    location: str | None = None
    project: str | None = None
    unit_cost_cents: int | None = None
    url: str | None = None


@runtime_checkable
class InventorySource(Protocol):
    """Something benchhud can read stock from and record consumption against."""

    @property
    def name(self) -> str:
        """Human label for the backend (shown in the UI)."""
        ...

    def list_low_stock(self) -> list[Part]: ...

    def search(self, query: str) -> list[Part]: ...

    def get_part(self, part_id: int) -> Part | None: ...

    def consume(self, part_id: int, quantity: int, ticket_ref: str | None = None) -> Part:
        """Record parts used; returns the updated part or raises
        :class:`InventoryWriteError`."""
        ...


class NullInventorySource:
    """Default: no inventory backend configured — benchhud runs standalone."""

    name = "none"

    def list_low_stock(self) -> list[Part]:
        return []

    def search(self, query: str) -> list[Part]:
        return []

    def get_part(self, part_id: int) -> Part | None:
        return None

    def consume(self, part_id: int, quantity: int, ticket_ref: str | None = None) -> Part:
        raise InventoryWriteError("no inventory backend configured")


@dataclass
class InMemoryInventorySource:
    """A source backed by a list — for tests, demos, and offline fallback."""

    parts: list[Part] = field(default_factory=list)
    name: str = "in-memory"

    def list_low_stock(self) -> list[Part]:
        return [p for p in self.parts if p.status in ("out", "low")]

    def search(self, query: str) -> list[Part]:
        needle = query.lower()
        return [
            p
            for p in self.parts
            if needle in p.description.lower() or needle in (p.mpn or "").lower()
        ]

    def get_part(self, part_id: int) -> Part | None:
        return next((p for p in self.parts if p.id == part_id), None)

    def consume(self, part_id: int, quantity: int, ticket_ref: str | None = None) -> Part:
        part = self.get_part(part_id)
        if part is None:
            raise InventoryWriteError(f"no part with id {part_id}")
        if quantity < 1:
            raise InventoryWriteError("quantity must be at least 1")
        if quantity > part.quantity_on_hand:
            raise InventoryWriteError(
                f"insufficient stock: {part.quantity_on_hand} on hand, {quantity} requested"
            )
        updated = replace(part, quantity_on_hand=part.quantity_on_hand - quantity)
        self.parts[self.parts.index(part)] = updated
        return updated
