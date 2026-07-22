"""Pluggable ticket sources.

benchhud is the *bench-side interface* to a shop ticketing system, not the
ticketing system itself. Intake (customer, device, symptoms, accessories) belongs
in a separate service — self-hosted on the bench box — and benchhud pulls a work
queue from it, linking each local job to a ticket by reference.

Everything goes through :class:`TicketSource`, so an in-house intake service is
just the first implementation and other backends can follow. With no source
configured (:class:`NullTicketSource`) benchhud works entirely standalone — the
local job store remains the bench log either way.

**Privacy note:** benchhud composites panels to a virtual camera for streaming.
Treat customer fields on :class:`Ticket` as sensitive — keep them out of anything
that renders into the video output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Ticket:
    """An intake ticket from a shop system.

    ``ref`` is the stable external identifier benchhud stores against a job.
    ``customer`` is sensitive — never render it into the stream surface.
    """

    ref: str
    device: str
    symptoms: str = ""
    customer: str = ""
    status: str = ""
    url: str | None = None


@runtime_checkable
class TicketSource(Protocol):
    """Something benchhud can pull a work queue from."""

    @property
    def name(self) -> str:
        """Human label for the backend (shown in the UI)."""
        ...

    def list_open_tickets(self) -> list[Ticket]: ...

    def get_ticket(self, ref: str) -> Ticket | None: ...


class NullTicketSource:
    """Default: no ticketing backend configured — benchhud runs standalone."""

    name = "none"

    def list_open_tickets(self) -> list[Ticket]:
        return []

    def get_ticket(self, ref: str) -> Ticket | None:
        return None


@dataclass
class InMemoryTicketSource:
    """A source backed by a list — for tests, demos, and offline fallback."""

    tickets: list[Ticket] = field(default_factory=list)
    name: str = "in-memory"

    def list_open_tickets(self) -> list[Ticket]:
        return list(self.tickets)

    def get_ticket(self, ref: str) -> Ticket | None:
        return next((t for t in self.tickets if t.ref == ref), None)
