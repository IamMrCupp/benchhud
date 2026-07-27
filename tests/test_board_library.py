"""Tests for the local board-view library and board_id on jobs (hardware-free)."""

from __future__ import annotations

import sqlite3

import pytest

from benchhud.boards.library import (
    DEFAULT_EXTENSIONS,
    DEFAULT_SCHEMATIC_EXTENSIONS,
    BoardLibrary,
)
from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.tickets.importer import import_ticket
from benchhud.tickets.source import Ticket


@pytest.fixture
def library(tmp_path) -> BoardLibrary:
    """A library with a couple of boards, one filed into a sub-folder."""
    (tmp_path / "820-00239.obv").write_text("x")
    (tmp_path / "820-00239.brd").write_text("x")
    (tmp_path / "051-01234.bdv").write_text("x")
    nested = tmp_path / "apple" / "macbook"
    nested.mkdir(parents=True)
    (nested / "820-99999.obv").write_text("x")
    (tmp_path / "notes.txt").write_text("not a board")
    return BoardLibrary(root=tmp_path)


# --- resolve / has -------------------------------------------------------


def test_resolve_finds_every_format_for_a_board(library) -> None:
    found = library.resolve("820-00239")
    assert sorted(b.format for b in found) == ["brd", "obv"]


def test_resolve_is_case_insensitive(tmp_path) -> None:
    (tmp_path / "ABC-1.obv").write_text("x")
    assert BoardLibrary(root=tmp_path).has("abc-1")


def test_resolve_searches_subfolders(library) -> None:
    assert library.has("820-99999")


def test_resolve_ignores_non_board_files(library) -> None:
    assert library.resolve("notes") == []


def test_resolve_blank_or_missing_board_id(library) -> None:
    assert library.resolve(None) == []
    assert library.resolve("  ") == []
    assert not library.has("nope")


# --- search / list -------------------------------------------------------


def test_search_matches_substring(library) -> None:
    assert {b.board_id for b in library.search("820")} == {"820-00239", "820-99999"}


def test_blank_search_lists_everything(library) -> None:
    assert len(library.search("")) == 4  # 2 formats for one board + 2 others


def test_list_all_is_sorted(library) -> None:
    ids = [b.board_id for b in library.list_all()]
    assert ids == sorted(ids)


# --- unconfigured / missing dir ------------------------------------------


def test_unconfigured_library_is_empty_not_an_error() -> None:
    empty = BoardLibrary.from_config("")
    assert not empty.configured
    assert empty.list_all() == []
    assert empty.resolve("820-00239") == []


def test_missing_directory_is_not_an_error(tmp_path) -> None:
    library = BoardLibrary(root=tmp_path / "does-not-exist")
    assert not library.configured
    assert library.search("anything") == []


def test_from_config_normalises_extensions() -> None:
    library = BoardLibrary.from_config("~/boards", [".OBV", "brd", "  "])
    assert library.extensions == ("obv", "brd")
    assert not str(library.root).startswith("~")  # expanded


def test_from_config_falls_back_to_default_extensions() -> None:
    assert BoardLibrary.from_config("/tmp/x", []).extensions == DEFAULT_EXTENSIONS


# --- missing() → the "boards to source" input ----------------------------


def test_missing_lists_only_boards_without_files(library) -> None:
    assert library.missing(["820-00239", "111-00000", "051-01234"]) == ["111-00000"]


def test_missing_dedupes_and_drops_blanks(library) -> None:
    assert library.missing(["111", "111", None, "  ", "222"]) == ["111", "222"]


# --- board_id on jobs ----------------------------------------------------


def test_import_persists_board_id() -> None:
    store = JobStore(":memory:")
    job = import_ticket(store, Ticket(ref="26-1", device="CDJ-3000", board_id="820-00239"))
    assert job.board_id == "820-00239"
    store.close()


def test_import_backfills_board_id_on_resync() -> None:
    """A job imported before intake sent board_id picks it up on re-import."""
    store = JobStore(":memory:")
    first = import_ticket(store, Ticket(ref="26-1", device="CDJ-3000"))
    assert first.board_id is None
    second = import_ticket(store, Ticket(ref="26-1", device="CDJ-3000", board_id="820-00239"))
    assert second.id == first.id
    assert second.board_id == "820-00239"
    assert len(store.list_jobs()) == 1
    store.close()


def test_local_job_has_no_board_id() -> None:
    store = JobStore(":memory:")
    assert store.create_job("walk-in").board_id is None
    store.close()


def test_migrates_database_without_board_id(tmp_path) -> None:
    """A jobs.db from before board docs still opens and can be linked."""
    path = tmp_path / "old.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL,
            serial TEXT NOT NULL DEFAULT '', customer TEXT NOT NULL DEFAULT '',
            symptoms TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, external_ref TEXT);
        CREATE TABLE job_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, text TEXT NOT NULL);
        """
    )
    legacy.execute("INSERT INTO jobs (device, created_at, updated_at) VALUES ('old', 't', 't')")
    legacy.commit()
    legacy.close()

    migrated = JobStore(path)
    try:
        assert migrated.list_jobs()[0].board_id is None
        job = import_ticket(migrated, Ticket(ref="26-2", device="x", board_id="820-1"))
        assert job.board_id == "820-1"
    finally:
        migrated.close()


# --- config --------------------------------------------------------------


def test_boards_config_round_trips() -> None:
    cfg = Config()
    cfg.boards.library_dir = "/srv/boards"
    cfg.boards.extensions = ["obv"]
    restored = Config.from_dict(cfg.to_dict())
    assert restored.boards.library_dir == "/srv/boards"
    assert restored.boards.extensions == ["obv"]


def test_boards_config_defaults() -> None:
    cfg = Config.from_dict({})
    assert cfg.boards.library_dir == ""
    assert cfg.boards.extensions == ["obv", "brd", "bdv"]


# --- schematics + viewer config ------------------------------------------


def test_resolve_schematic_finds_the_pdf(tmp_path) -> None:
    (tmp_path / "820-00239.brd").write_text("x")
    (tmp_path / "820-00239.pdf").write_text("x")
    lib = BoardLibrary(root=tmp_path)
    schem = lib.resolve_schematic("820-00239")
    assert [s.format for s in schem] == ["pdf"]
    assert lib.has_schematic("820-00239")


def test_schematic_is_not_listed_as_a_boardview(tmp_path) -> None:
    (tmp_path / "820-00239.pdf").write_text("x")
    lib = BoardLibrary(root=tmp_path)
    assert lib.resolve("820-00239") == []  # a PDF is not a board file
    assert lib.list_all() == []
    assert lib.has_schematic("820-00239")


def test_no_schematic_when_absent(tmp_path) -> None:
    (tmp_path / "051-01234.bdv").write_text("x")
    lib = BoardLibrary(root=tmp_path)
    assert not lib.has_schematic("051-01234")
    assert lib.resolve_schematic("051-01234") == []


def test_from_config_sets_schematic_extensions() -> None:
    lib = BoardLibrary.from_config("/tmp/x", ["brd"], [".PDF", "svg"])
    assert lib.schematic_extensions == ("pdf", "svg")


def test_from_config_defaults_schematic_extensions() -> None:
    assert BoardLibrary.from_config("/tmp/x").schematic_extensions == DEFAULT_SCHEMATIC_EXTENSIONS


def test_boards_config_viewer_and_schematics_round_trip() -> None:
    cfg = Config()
    cfg.boards.viewer = "flexbv"
    cfg.boards.schematic_extensions = ["pdf", "svg"]
    restored = Config.from_dict(cfg.to_dict())
    assert restored.boards.viewer == "flexbv"
    assert restored.boards.schematic_extensions == ["pdf", "svg"]


def test_boards_config_new_defaults() -> None:
    cfg = Config.from_dict({})
    assert cfg.boards.viewer == "auto"
    assert cfg.boards.schematic_extensions == ["pdf"]
