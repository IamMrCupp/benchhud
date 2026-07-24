"""Tests for the repair-job store."""

from __future__ import annotations

import pytest

from benchhud.jobs.store import JobStatus, JobStore, default_db_path


def _clock():
    """Deterministic increasing timestamps."""
    ticks = iter(f"2026-07-19T10:{n:02d}:00+00:00" for n in range(60))
    return lambda: next(ticks)


@pytest.fixture
def store() -> JobStore:
    s = JobStore(":memory:", now=_clock())
    yield s
    s.close()


def test_create_and_get(store) -> None:
    job = store.create_job("MacBook A1706", serial="C02X", symptoms="no power")
    fetched = store.get_job(job.id)
    assert fetched == job
    assert fetched.device == "MacBook A1706"
    assert fetched.symptoms == "no power"
    assert fetched.status == JobStatus.OPEN
    assert fetched.created_at == fetched.updated_at


def test_get_missing_returns_none(store) -> None:
    assert store.get_job(999) is None


def test_list_jobs_newest_first(store) -> None:
    store.create_job("board A")
    store.create_job("board B")
    assert [j.device for j in store.list_jobs()] == ["board B", "board A"]


def test_list_jobs_filtered_by_status(store) -> None:
    store.create_job("open one")
    done = store.create_job("done one")
    store.update_job(done.id, status=JobStatus.DONE)
    assert [j.device for j in store.list_jobs(JobStatus.DONE)] == ["done one"]
    assert [j.device for j in store.list_jobs(JobStatus.OPEN)] == ["open one"]


def test_update_job_bumps_updated_at(store) -> None:
    job = store.create_job("board")
    updated = store.update_job(job.id, status=JobStatus.IN_PROGRESS, symptoms="shorted")
    assert updated.status == JobStatus.IN_PROGRESS
    assert updated.symptoms == "shorted"
    assert updated.updated_at != job.updated_at
    assert updated.created_at == job.created_at


def test_update_rejects_unknown_field(store) -> None:
    job = store.create_job("board")
    with pytest.raises(ValueError, match="unknown job field"):
        store.update_job(job.id, bogus="x")


def test_notes_timeline_oldest_first(store) -> None:
    job = store.create_job("board")
    store.add_note(job.id, "measured 0.5 ohm on the rail")
    store.add_note(job.id, "replaced U3300")
    notes = store.list_notes(job.id)
    assert [n.text for n in notes] == [
        "measured 0.5 ohm on the rail",
        "replaced U3300",
    ]
    assert all(n.job_id == job.id for n in notes)


def test_delete_job_cascades_notes(store) -> None:
    job = store.create_job("board")
    store.add_note(job.id, "note")
    store.delete_job(job.id)
    assert store.get_job(job.id) is None
    assert store.list_notes(job.id) == []


def test_persists_to_disk(tmp_path) -> None:
    path = tmp_path / "nested" / "jobs.db"
    first = JobStore(path, now=_clock())
    job = first.create_job("persisted board", symptoms="backlight dead")
    first.add_note(job.id, "checked fuse")
    first.close()

    reopened = JobStore(path)
    try:
        assert [j.device for j in reopened.list_jobs()] == ["persisted board"]
        assert [n.text for n in reopened.list_notes(job.id)] == ["checked fuse"]
    finally:
        reopened.close()


def test_default_db_path_sits_beside_config(monkeypatch, tmp_path) -> None:
    from benchhud import config as cfg

    monkeypatch.setenv(cfg.ENV_VAR, str(tmp_path / "conf" / "config.toml"))
    assert default_db_path() == tmp_path / "conf" / "jobs.db"


def test_note_attachment_round_trip(store) -> None:
    job = store.create_job("board")
    store.add_note(job.id, "thermal capture", attachment="/tmp/cap.png")
    note = store.list_notes(job.id)[0]
    assert note.attachment == "/tmp/cap.png"


def test_note_without_attachment_is_none(store) -> None:
    job = store.create_job("board")
    store.add_note(job.id, "plain note")
    assert store.list_notes(job.id)[0].attachment is None


def test_attachments_dir_beside_db(tmp_path) -> None:
    s = JobStore(tmp_path / "jobs.db")
    try:
        job = s.create_job("board")
        d = s.attachments_dir(job.id)
        assert d == tmp_path / "job_attachments" / str(job.id)
        assert d.is_dir()
    finally:
        s.close()


def test_in_memory_store_has_no_attachments_dir(store) -> None:
    job = store.create_job("board")
    assert store.attachments_dir(job.id) is None


def test_migrates_older_database(tmp_path) -> None:
    """A db created before the attachment column still opens and works."""
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
    legacy.execute("INSERT INTO job_notes (job_id, created_at, text) VALUES (1, 't', 'old note')")
    legacy.commit()
    legacy.close()

    migrated = JobStore(path)
    try:
        assert [j.device for j in migrated.list_jobs()] == ["legacy"]
        note = migrated.list_notes(1)[0]
        assert note.text == "old note"
        assert note.attachment is None  # column added by the migration
        migrated.add_note(1, "new note", attachment="/tmp/x.png")
        assert migrated.list_notes(1)[1].attachment == "/tmp/x.png"
    finally:
        migrated.close()


# --- find_related (per-board history) ---------------------------------------


def test_find_related_matches_by_serial(store) -> None:
    a = store.create_job("iPhone 12")
    store.update_job(a.id, serial="SNX1")
    b = store.create_job("iPhone 12")
    store.update_job(b.id, serial="SNX1")
    other = store.create_job("iPhone 12")
    store.update_job(other.id, serial="DIFFERENT")

    related = store.find_related(store.get_job(b.id))

    assert [j.id for j in related] == [a.id]  # same serial, excludes self and others


def test_find_related_falls_back_to_device_when_no_serial(store) -> None:
    a = store.create_job("MacBook A1707")
    b = store.create_job("MacBook A1707")  # no serial on either
    store.create_job("MacBook A1932")

    related = store.find_related(store.get_job(b.id))

    assert [j.id for j in related] == [a.id]


def test_find_related_serial_beats_device(store) -> None:
    """With a serial set, a different board of the same model must NOT match."""
    same_model_no_serial = store.create_job("iPad")
    target = store.create_job("iPad")
    store.update_job(target.id, serial="ONLY-ME")

    assert store.find_related(store.get_job(target.id)) == []
    assert same_model_no_serial.id  # exists, but not matched (no shared serial)


def test_find_related_empty_when_nothing_matches(store) -> None:
    solo = store.create_job("unique board")
    assert store.find_related(solo) == []


def test_find_related_ignores_blank_device_and_serial(store) -> None:
    """A job with neither device nor serial shouldn't sweep in every blank job."""
    blank1 = store.create_job("x")
    store.update_job(blank1.id, device="")
    store.create_job("y")  # unrelated

    assert store.find_related(store.get_job(blank1.id)) == []
