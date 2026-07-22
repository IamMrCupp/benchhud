"""Local-first repair-job store (SQLite).

A board-repair bench is job-shaped: a board arrives with symptoms, you measure
and replace things, it ends up fixed or scrapped. This keeps that on the bench —
no server, no account, works offline — in a SQLite file beside ``config.toml``.

Each :class:`Job` carries a :class:`JobNote` timeline, which is what later lets a
"capture to job" action append scope/thermal frames and live meter readings as
you work.

The connection is used from the GUI thread. Both the db path and the clock are
injectable so tests run in-memory and deterministically.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device     TEXT NOT NULL,
    serial     TEXT NOT NULL DEFAULT '',
    customer   TEXT NOT NULL DEFAULT '',
    symptoms   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    text       TEXT NOT NULL
);
"""

# Fields a caller may update on a job (whitelist — never interpolate raw input).
_UPDATABLE = ("device", "serial", "customer", "symptoms", "status")


class JobStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"  # on parts, or on the customer
    DONE = "done"
    SCRAPPED = "scrapped"


@dataclass(frozen=True)
class Job:
    id: int
    device: str
    serial: str
    customer: str
    symptoms: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class JobNote:
    id: int
    job_id: int
    created_at: str
    text: str


def default_db_path() -> Path:
    """``jobs.db`` beside the live config file."""
    from benchhud.config import default_config_path

    return default_config_path().parent / "jobs.db"


class JobStore:
    """SQLite-backed repair-job store."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC).isoformat(timespec="seconds"))
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- jobs ------------------------------------------------------------

    def create_job(
        self,
        device: str,
        *,
        serial: str = "",
        customer: str = "",
        symptoms: str = "",
        status: str = JobStatus.OPEN,
    ) -> Job:
        stamp = self._now()
        cur = self._conn.execute(
            "INSERT INTO jobs (device, serial, customer, symptoms, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (device, serial, customer, symptoms, str(status), stamp, stamp),
        )
        self._conn.commit()
        job = self.get_job(int(cur.lastrowid))
        assert job is not None  # just inserted
        return job

    def get_job(self, job_id: int) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list_jobs(self, status: str | None = None) -> list[Job]:
        """Jobs, newest first; optionally filtered by status."""
        if status is None:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC", (str(status),)
            ).fetchall()
        return [_job(r) for r in rows]

    def update_job(self, job_id: int, **fields: str) -> Job | None:
        """Update whitelisted fields and bump ``updated_at``."""
        unknown = set(fields) - set(_UPDATABLE)
        if unknown:
            raise ValueError(f"cannot update unknown job field(s): {sorted(unknown)}")
        if not fields:
            return self.get_job(job_id)

        # Column names are interpolated, but only from the _UPDATABLE whitelist
        # checked above — all values stay parameterised.
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [str(v) for v in fields.values()]
        self._conn.execute(
            f"UPDATE jobs SET {assignments}, updated_at = ? WHERE id = ?",
            (*values, self._now(), job_id),
        )
        self._conn.commit()
        return self.get_job(job_id)

    def delete_job(self, job_id: int) -> None:
        """Delete a job and its notes."""
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()

    # --- notes -----------------------------------------------------------

    def add_note(self, job_id: int, text: str) -> JobNote:
        stamp = self._now()
        cur = self._conn.execute(
            "INSERT INTO job_notes (job_id, created_at, text) VALUES (?, ?, ?)",
            (job_id, stamp, text),
        )
        self._conn.commit()
        return JobNote(id=int(cur.lastrowid), job_id=job_id, created_at=stamp, text=text)

    def list_notes(self, job_id: int) -> list[JobNote]:
        """Notes for a job, oldest first (a timeline)."""
        rows = self._conn.execute(
            "SELECT * FROM job_notes WHERE job_id = ? ORDER BY id ASC", (job_id,)
        ).fetchall()
        return [
            JobNote(id=r["id"], job_id=r["job_id"], created_at=r["created_at"], text=r["text"])
            for r in rows
        ]


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        device=row["device"],
        serial=row["serial"],
        customer=row["customer"],
        symptoms=row["symptoms"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
