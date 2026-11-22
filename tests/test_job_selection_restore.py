"""The selected job survives a restart (#497).

Seen on the bench: an upgrade restarted benchhud mid-job, the job card on
stream fell back to "On the bench", and anything filed against "the selected
job" had nowhere to go until someone noticed and selected it again.

The rule pinned here: the selection is remembered when it changes, not at
quit, so a crash keeps it; and it's put back at start only when the job is
still there and not finished.
"""

from __future__ import annotations

import sqlite3

import pytest

from benchhud.config import Config
from benchhud.jobs.store import FINISHED_STATUSES, JobStatus, JobStore
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.jobs import JobsPanel


@pytest.fixture
def store() -> JobStore:
    s = JobStore(":memory:")
    yield s
    s.close()


# --- the store remembers -------------------------------------------------------


def test_nothing_is_remembered_to_begin_with(store) -> None:
    assert store.selected_job_id() is None
    assert store.restorable_job() is None


def test_the_selected_job_is_remembered_and_forgotten(store) -> None:
    job = store.create_job("MacBook A1706")
    store.set_selected_job(job.id)
    assert store.selected_job_id() == job.id
    store.set_selected_job(None)
    assert store.selected_job_id() is None


def test_a_later_selection_replaces_the_earlier_one(store) -> None:
    first, second = store.create_job("one"), store.create_job("two")
    store.set_selected_job(first.id)
    store.set_selected_job(second.id)
    assert store.selected_job_id() == second.id


def test_it_is_on_disk_the_moment_it_is_set(tmp_path) -> None:
    """A crash never runs the quit path, so nothing may wait for it."""
    path = tmp_path / "jobs.db"
    first = JobStore(path)
    job = first.create_job("MacBook A1706")
    first.set_selected_job(job.id)
    # No close(): a second connection, as after a crash, already sees it.
    second = JobStore(path)
    try:
        assert second.selected_job_id() == job.id
    finally:
        second.close()
        first.close()


def test_a_database_from_before_this_gains_the_table(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    old = JobStore(path)
    job = old.create_job("MacBook A1706")
    old._conn.execute("DROP TABLE app_state")
    old._conn.commit()
    old.close()
    upgraded = JobStore(path)
    try:
        assert upgraded.selected_job_id() is None
        upgraded.set_selected_job(job.id)
        assert upgraded.selected_job_id() == job.id
    finally:
        upgraded.close()


def test_a_value_that_is_not_a_job_id_reads_as_nothing(store) -> None:
    store._conn.execute("INSERT INTO app_state (key, value) VALUES ('selected_job', 'junk')")
    assert store.selected_job_id() is None
    assert store.restorable_job() is None


# --- which jobs come back ------------------------------------------------------


@pytest.mark.parametrize("status", [JobStatus.OPEN, JobStatus.IN_PROGRESS, JobStatus.WAITING])
def test_a_job_still_on_the_bench_is_restorable(store, status) -> None:
    job = store.create_job("MacBook A1706")
    store.update_job(job.id, status=status)
    store.set_selected_job(job.id)
    assert store.restorable_job().id == job.id


@pytest.mark.parametrize("status", sorted(FINISHED_STATUSES))
def test_a_finished_job_is_not_put_back(store, status) -> None:
    """The cautious half: a job that's off the bench doesn't go back on stream."""
    job = store.create_job("MacBook A1706")
    store.set_selected_job(job.id)
    store.update_job(job.id, status=status)
    assert store.restorable_job() is None


def test_a_deleted_job_is_not_put_back(store) -> None:
    job = store.create_job("MacBook A1706")
    store.set_selected_job(job.id)
    store.delete_job(job.id)
    assert store.restorable_job() is None


def test_finished_means_done_or_scrapped() -> None:
    assert {JobStatus.DONE, JobStatus.SCRAPPED} == FINISHED_STATUSES


# --- the panel saves it, and puts it back --------------------------------------


def test_selecting_a_job_is_remembered_at_once(qtbot, store) -> None:
    first, second = store.create_job("one"), store.create_job("two")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.select_job(first.id)
    assert store.selected_job_id() == first.id
    panel.select_job(second.id)
    assert store.selected_job_id() == second.id


def test_a_new_job_is_the_remembered_one(qtbot, store) -> None:
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    job = panel.create_job_named("MacBook A1706")
    assert store.selected_job_id() == job.id


def test_saving_or_reloading_the_list_does_not_forget_it(qtbot, store) -> None:
    """The list is emptied and refilled on every save; that isn't a deselect."""
    job = store.create_job("MacBook A1706")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    panel.select_job(job.id)
    panel.refresh_jobs()
    assert store.selected_job_id() == job.id
    panel.select_job(job.id)
    panel.save_current()
    assert store.selected_job_id() == job.id


def test_a_fresh_panel_has_nothing_selected_until_asked(qtbot, store) -> None:
    job = store.create_job("MacBook A1706")
    store.set_selected_job(job.id)
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    assert panel.selected_job_id() is None
    assert panel.restore_selection() is True
    assert panel.selected_job_id() == job.id


def test_restoring_tells_the_listeners(qtbot, store) -> None:
    """The job card, the readings and the board reference follow ``job_changed``."""
    job = store.create_job("MacBook A1706")
    store.set_selected_job(job.id)
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.job_changed, timeout=1000):
        panel.restore_selection()
    assert panel.device_edit.text() == "MacBook A1706"


def test_restoring_with_nothing_remembered_selects_nothing(qtbot, store) -> None:
    store.create_job("MacBook A1706")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    assert panel.restore_selection() is False
    assert panel.selected_job_id() is None


def test_restoring_a_finished_job_selects_nothing(qtbot, store) -> None:
    job = store.create_job("MacBook A1706")
    store.set_selected_job(job.id)
    store.update_job(job.id, status=JobStatus.DONE)
    panel = JobsPanel(store)
    qtbot.addWidget(panel)
    assert panel.restore_selection() is False
    assert panel.selected_job_id() is None


def test_a_store_that_cannot_write_still_selects(qtbot, store, monkeypatch, caplog) -> None:
    """A read-only disk costs the memory, not the click."""
    job = store.create_job("MacBook A1706")
    panel = JobsPanel(store)
    qtbot.addWidget(panel)

    def refuse(_job_id) -> None:
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(store, "set_selected_job", refuse)
    panel.select_job(job.id)
    assert panel.selected_job_id() == job.id
    assert "could not remember" in caplog.text


# --- the whole thing: a restart -------------------------------------------------


def _window(qtbot, path) -> tuple[MainWindow, JobStore]:
    store = JobStore(path)
    window = MainWindow(Config(), job_store=store)
    qtbot.addWidget(window)
    return window, store


def test_the_selected_job_is_selected_again_after_a_restart(qtbot, tmp_path) -> None:
    path = tmp_path / "jobs.db"
    window, store = _window(qtbot, path)
    window.jobs_panel.create_job_named("older job")
    job = window.jobs_panel.create_job_named("MacBook A1706", external_ref="26-0001")
    window.jobs_panel.create_job_named("newer job")
    window.jobs_panel.select_job(job.id)
    window.close()
    store.close()

    again, store = _window(qtbot, path)
    try:
        assert again.jobs_panel.selected_job_id() == job.id
        # What bills parts to the job reads the selection, so it's back too.
        assert again.selected_job_ref() == "26-0001"
    finally:
        again.close()
        store.close()


def test_it_comes_back_after_a_crash_too(qtbot, tmp_path) -> None:
    """No close, no quit path: the first window is simply abandoned."""
    path = tmp_path / "jobs.db"
    window, crashed_store = _window(qtbot, path)
    job = window.jobs_panel.create_job_named("MacBook A1706")

    again, store = _window(qtbot, path)
    try:
        assert again.jobs_panel.selected_job_id() == job.id
    finally:
        again.close()
        store.close()
        window.close()
        crashed_store.close()


def test_a_job_finished_before_the_restart_stays_off(qtbot, tmp_path) -> None:
    path = tmp_path / "jobs.db"
    window, store = _window(qtbot, path)
    job = window.jobs_panel.create_job_named("MacBook A1706")
    store.update_job(job.id, status=JobStatus.SCRAPPED)
    window.close()
    store.close()

    again, store = _window(qtbot, path)
    try:
        assert again.jobs_panel.selected_job_id() is None
    finally:
        again.close()
        store.close()


def test_the_job_card_shows_the_restored_job(qtbot, tmp_path) -> None:
    """What the stream sees: the card has the job, not "On the bench"."""
    path = tmp_path / "jobs.db"
    window, store = _window(qtbot, path)
    job = window.jobs_panel.create_job_named("MacBook A1706")
    window.close()
    store.close()

    config = Config()
    config.browser_sources.enabled = True
    config.browser_sources.port = 0
    store = JobStore(path)
    again = MainWindow(config, job_store=store, control_file=tmp_path / "control.json")
    qtbot.addWidget(again)
    again.start_browser_sources()
    try:
        # What `benchhud job list` stars, and what the card page was handed.
        assert again._control_list_jobs()["selected"] == job.id
        _version, card = again._browser_hub.latest("job")
        assert card["job"]["device"] == "MacBook A1706"
    finally:
        again.stop_browser_sources()
        again.close()
        store.close()
