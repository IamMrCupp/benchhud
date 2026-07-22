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
