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
    updated_at TEXT NOT NULL,
    external_ref TEXT,
    board_id TEXT
);
CREATE TABLE IF NOT EXISTS job_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    text       TEXT NOT NULL,
    attachment TEXT
);
CREATE TABLE IF NOT EXISTS readings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    point      TEXT NOT NULL,
    mode       TEXT NOT NULL,
    value      REAL,
    status     TEXT,
    unit       TEXT NOT NULL DEFAULT '',
    reference  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

# Fields a caller may update on a job (whitelist — never interpolate raw input).
_UPDATABLE = ("device", "serial", "customer", "symptoms", "status", "external_ref", "board_id")


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
    # Reference to a ticket in an external shop system, when linked.
    external_ref: str | None = None
    # The board's part number (e.g. 820-00239) — non-PII, and the key the board
    # library resolves against. Stored locally so it works with intake offline.
    board_id: str | None = None


@dataclass(frozen=True)
class JobNote:
    id: int
    job_id: int
    created_at: str
    text: str
    attachment: str | None = None  # path to a captured image, if any


@dataclass(frozen=True)
class Reading:
    """One measurement at a test point (#235).

    ``value`` is None when the meter showed a status instead (``OL``: open).
    ``reference`` marks a known-good board's reading: those become the
    comparison for every later job on the same board number.
    """

    id: int
    job_id: int
    point: str
    mode: str  # diode | ohm | vdc, or the meter's own token for anything else
    value: float | None
    status: str | None
    unit: str
    reference: bool
    created_at: str


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
        self._migrate()
        self._conn.commit()
        self._root = None if str(path) == ":memory:" else Path(path).parent

    def _migrate(self) -> None:
        """Bring an older database up to the current schema (additive only)."""
        self._add_column_if_missing("job_notes", "attachment", "TEXT")
        self._add_column_if_missing("jobs", "external_ref", "TEXT")
        self._add_column_if_missing("jobs", "board_id", "TEXT")

    def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def attachments_dir(self, job_id: int) -> Path | None:
        """Directory for a job's captured images (``None`` for in-memory stores)."""
        if self._root is None:
            return None
        path = self._root / "job_attachments" / str(job_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

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
        external_ref: str | None = None,
        board_id: str | None = None,
    ) -> Job:
        stamp = self._now()
        cur = self._conn.execute(
            "INSERT INTO jobs (device, serial, customer, symptoms, status,"
            " created_at, updated_at, external_ref, board_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device, serial, customer, symptoms, str(status), stamp, stamp, external_ref, board_id),
        )
        self._conn.commit()
        job = self.get_job(int(cur.lastrowid))
        assert job is not None  # just inserted
        return job

    def get_job(self, job_id: int) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def find_by_external_ref(self, ref: str) -> Job | None:
        """The job linked to an external ticket reference, if any."""
        row = self._conn.execute("SELECT * FROM jobs WHERE external_ref = ?", (ref,)).fetchone()
        return _job(row) if row else None

    def find_related(self, job: Job) -> list[Job]:
        """Prior jobs for the same board — matched by serial, else by device.

        "I've seen this board before": a non-empty serial is the strong signal
        (same physical board), so it wins; otherwise fall back to the device
        model. The job itself is always excluded. Newest first.
        """
        serial = job.serial.strip()
        if serial:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE id != ? AND serial != '' AND serial = ? ORDER BY id DESC",
                (job.id, serial),
            ).fetchall()
        else:
            device = job.device.strip()
            if not device:
                return []
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE id != ? AND device = ? ORDER BY id DESC",
                (job.id, device),
            ).fetchall()
        return [_job(r) for r in rows]

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

    def add_note(self, job_id: int, text: str, attachment: str | None = None) -> JobNote:
        stamp = self._now()
        cur = self._conn.execute(
            "INSERT INTO job_notes (job_id, created_at, text, attachment) VALUES (?, ?, ?, ?)",
            (job_id, stamp, text, attachment),
        )
        self._conn.commit()
        return JobNote(
            id=int(cur.lastrowid),
            job_id=job_id,
            created_at=stamp,
            text=text,
            attachment=attachment,
        )

    def list_notes(self, job_id: int) -> list[JobNote]:
        """Notes for a job, oldest first (a timeline)."""
        rows = self._conn.execute(
            "SELECT * FROM job_notes WHERE job_id = ? ORDER BY id ASC", (job_id,)
        ).fetchall()
        return [
            JobNote(
                id=r["id"],
                job_id=r["job_id"],
                created_at=r["created_at"],
                text=r["text"],
                attachment=r["attachment"],
            )
            for r in rows
        ]

    # --- readings (#235) -------------------------------------------------

    def add_reading(
        self,
        job_id: int,
        point: str,
        mode: str,
        value: float | None,
        *,
        status: str | None = None,
        unit: str = "",
        reference: bool = False,
    ) -> Reading:
        point = normalize_point(point)
        if not point:
            raise ValueError("a reading needs a test point")
        stamp = self._now()
        cur = self._conn.execute(
            "INSERT INTO readings (job_id, point, mode, value, status, unit, reference,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, point, mode, value, status, unit, int(reference), stamp),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM readings WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _reading(row)

    def list_readings(self, job_id: int) -> list[Reading]:
        """A job's readings, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM readings WHERE job_id = ? ORDER BY id ASC", (job_id,)
        ).fetchall()
        return [_reading(r) for r in rows]

    def reference_readings(self, board_id: str, *, exclude_job: int | None = None) -> list[Reading]:
        """Known-good readings for a board number, from every job on it."""
        board_id = board_id.strip()
        if not board_id:
            return []
        rows = self._conn.execute(
            "SELECT readings.* FROM readings JOIN jobs ON jobs.id = readings.job_id"
            " WHERE jobs.board_id = ? AND readings.reference = 1 AND readings.job_id != ?"
            " ORDER BY readings.id ASC",
            (board_id, -1 if exclude_job is None else exclude_job),
        ).fetchall()
        return [_reading(r) for r in rows]

    def set_readings_reference(self, job_id: int, reference: bool) -> None:
        """Mark all of a job's readings known-good (or not): after the fix is proven."""
        self._conn.execute(
            "UPDATE readings SET reference = ? WHERE job_id = ?", (int(reference), job_id)
        )
        self._conn.commit()

    def delete_reading(self, reading_id: int) -> None:
        self._conn.execute("DELETE FROM readings WHERE id = ?", (reading_id,))
        self._conn.commit()


def _reading(row: sqlite3.Row) -> Reading:
    return Reading(
        id=row["id"],
        job_id=row["job_id"],
        point=row["point"],
        mode=row["mode"],
        value=row["value"],
        status=row["status"],
        unit=row["unit"],
        reference=bool(row["reference"]),
        created_at=row["created_at"],
    )


def normalize_point(point: str) -> str:
    """Test points compare case-insensitively: ``pp3v3_s5`` is ``PP3V3_S5``."""
    return " ".join(point.split()).upper()


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
        external_ref=row["external_ref"],
        board_id=row["board_id"],
    )
