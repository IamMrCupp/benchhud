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
        return existing
    return store.create_job(
        ticket.device,
        customer=ticket.customer,
        symptoms=ticket.symptoms,
        external_ref=ticket.ref,
    )
