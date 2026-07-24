"""Bring a ticket from a shop system onto the bench as a local job."""

from __future__ import annotations

from benchhud.jobs.store import Job, JobStore
from benchhud.tickets.source import Ticket


def import_ticket(store: JobStore, ticket: Ticket) -> Job:
    """Create (or return) the local bench job for ``ticket``.

    Importing the same ticket twice returns the existing job rather than
    duplicating it, so re-syncing a work queue is safe.
    """
    existing = store.find_by_external_ref(ticket.ref)
    if existing is not None:
        # Backfill the board id if the ticket has one and the job doesn't — a job
        # imported before intake started sending board_id picks it up on re-sync.
        if ticket.board_id and not existing.board_id:
            updated = store.update_job(existing.id, board_id=ticket.board_id)
            if updated is not None:
                return updated
        return existing
    return store.create_job(
        ticket.device,
        customer=ticket.customer,
        symptoms=ticket.symptoms,
        external_ref=ticket.ref,
        board_id=ticket.board_id,
    )
