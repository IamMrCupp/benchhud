"""Tests for the ticket seam and importing tickets onto the bench."""

from __future__ import annotations

import pytest

from benchhud.jobs.store import JobStore
from benchhud.tickets.importer import import_ticket
from benchhud.tickets.source import (
    InMemoryTicketSource,
    NullTicketSource,
    Ticket,
    TicketSource,
)


@pytest.fixture
def store() -> JobStore:
    s = JobStore(":memory:")
    yield s
    s.close()


def _ticket(ref: str = "T-100") -> Ticket:
    return Ticket(
        ref=ref,
        device="MacBook A1706",
        symptoms="no power",
        customer="Jill",
        status="intake",
        url=f"http://repair-bench:8000/tickets/{ref}",
    )


# --- sources ---------------------------------------------------------------


def test_null_source_satisfies_protocol_and_is_empty() -> None:
    source = NullTicketSource()
    assert isinstance(source, TicketSource)
    assert source.list_open_tickets() == []
    assert source.get_ticket("T-100") is None
    assert source.name == "none"


def test_in_memory_source_lists_and_looks_up() -> None:
    source = InMemoryTicketSource([_ticket("T-1"), _ticket("T-2")])
    assert isinstance(source, TicketSource)
    assert [t.ref for t in source.list_open_tickets()] == ["T-1", "T-2"]
    assert source.get_ticket("T-2").ref == "T-2"
    assert source.get_ticket("nope") is None


# --- importing -------------------------------------------------------------


def test_import_creates_a_linked_job(store) -> None:
    job = import_ticket(store, _ticket())
    assert job.device == "MacBook A1706"
    assert job.symptoms == "no power"
    assert job.customer == "Jill"
    assert job.external_ref == "T-100"


def test_import_is_idempotent(store) -> None:
    first = import_ticket(store, _ticket())
    second = import_ticket(store, _ticket())  # re-syncing the queue
    assert second.id == first.id
    assert len(store.list_jobs()) == 1


def test_distinct_tickets_create_distinct_jobs(store) -> None:
    import_ticket(store, _ticket("T-1"))
    import_ticket(store, _ticket("T-2"))
    assert len(store.list_jobs()) == 2


def test_find_by_external_ref(store) -> None:
    job = import_ticket(store, _ticket("T-77"))
    assert store.find_by_external_ref("T-77").id == job.id
    assert store.find_by_external_ref("missing") is None


def test_local_jobs_have_no_ref(store) -> None:
    job = store.create_job("walk-in board")
    assert job.external_ref is None


def test_external_ref_survives_reopen(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    first = JobStore(path)
    import_ticket(first, _ticket("T-9"))
    first.close()

    reopened = JobStore(path)
    try:
        assert reopened.find_by_external_ref("T-9") is not None
    finally:
        reopened.close()


def test_migrates_database_without_external_ref(tmp_path) -> None:
    """A jobs.db from before ticketing still opens and can be linked."""
    import sqlite3

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL,
            serial TEXT NOT NULL DEFAULT '', customer TEXT NOT NULL DEFAULT '',
            symptoms TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE job_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, text TEXT NOT NULL);
        """
    )
    legacy.execute("INSERT INTO jobs (device, created_at, updated_at) VALUES ('legacy', 't', 't')")
    legacy.commit()
    legacy.close()

    migrated = JobStore(path)
    try:
        assert [j.device for j in migrated.list_jobs()] == ["legacy"]
        assert migrated.list_jobs()[0].external_ref is None
        job = import_ticket(migrated, _ticket("T-new"))
        assert job.external_ref == "T-new"
    finally:
        migrated.close()
