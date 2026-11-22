"""Tests for the datasheet library: a chip's document fingerprints, found on disk.

Pure filesystem work against ``tmp_path``: no Qt, no network, nothing opened.
Every document here is made up — the "PDFs" are a few bytes of text, and
"Example Semi" and its parts don't exist.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

import pytest

from benchhud.chips import Chip, Document
from benchhud.chips import datasheets as datasheets_module
from benchhud.chips.datasheets import (
    ChipDocument,
    DatasheetFile,
    DatasheetLibrary,
    MatchedBy,
    name_key,
    open_document,
)


def _write(folder: Path, name: str, content: bytes) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _doc(filename: str, *contents: bytes, kind: str = "datasheet") -> Document:
    """A fingerprint for a document whose known copies hold ``contents``."""
    return Document(kind=kind, filename=filename, sha256=tuple(_sha(c) for c in contents))


REV_A = b"%PDF made-up EX1234 revision A"
REV_B = b"%PDF made-up EX1234 revision B"
OTHER = b"%PDF made-up something else entirely"


@pytest.fixture
def hashed(monkeypatch) -> list[Path]:
    """Every path the library hashes, in order — hashing is the cost to watch."""
    seen: list[Path] = []
    real = datasheets_module._sha256

    def counting(path: Path) -> str:
        seen.append(path)
        return real(path)

    monkeypatch.setattr(datasheets_module, "_sha256", counting)
    return seen


# --- names -------------------------------------------------------------------


def test_names_compare_by_stem_without_case() -> None:
    assert name_key("EX1234.pdf") == name_key("ex1234.PDF") == "ex1234"
    assert name_key("docs/EX1234.pdf") == "ex1234"
    assert name_key(r"C:\sheets\EX1234.pdf") == "ex1234"
    assert name_key("EX1234") == "ex1234"


def test_a_blank_name_matches_nothing() -> None:
    assert name_key("") == name_key("   ") == name_key(None) == ""


# --- no library, or not much of one -------------------------------------------


def test_an_unconfigured_library_finds_nothing() -> None:
    library = DatasheetLibrary()
    assert not library.configured
    assert library.files() == ()
    assert library.find(_doc("EX1234.pdf", REV_A)) is None


def test_a_folder_that_is_gone_finds_nothing_and_says_so_once(tmp_path, caplog) -> None:
    library = DatasheetLibrary(tmp_path / "not-here")
    with caplog.at_level(logging.WARNING):
        assert library.find(_doc("EX1234.pdf", REV_A)) is None
        assert library.find(_doc("EX1234.pdf", REV_A)) is None
    assert library.configured
    assert len([r for r in caplog.records if "not a folder" in r.message]) == 1


def test_a_file_where_the_folder_should_be_finds_nothing(tmp_path) -> None:
    not_a_folder = _write(tmp_path, "datasheets", b"oops")
    assert DatasheetLibrary(not_a_folder).find(_doc("EX1234.pdf", REV_A)) is None


def test_from_config_takes_blank_as_not_configured() -> None:
    assert not DatasheetLibrary.from_config("").configured
    assert not DatasheetLibrary.from_config("   ").configured


def test_from_config_expands_the_home_folder() -> None:
    assert DatasheetLibrary.from_config("~/datasheets").root == Path.home() / "datasheets"


def test_from_config_survives_a_user_that_does_not_exist() -> None:
    library = DatasheetLibrary.from_config("~no-such-user-here-9f3a/datasheets")
    assert library.find(_doc("EX1234.pdf", REV_A)) is None


# --- by hash, then by name ----------------------------------------------------


def test_a_file_is_found_by_hash_whatever_it_is_called(tmp_path) -> None:
    held = _write(tmp_path, "downloaded (3).pdf", REV_A)
    found = DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A))
    assert found == DatasheetFile(held, MatchedBy.HASH)
    assert found.exact


def test_any_of_the_documents_hashes_will_do(tmp_path) -> None:
    """Two sites' copies of one revision differ byte for byte: either is it."""
    held = _write(tmp_path, "whatever.pdf", REV_B)
    found = DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A, REV_B))
    assert found == DatasheetFile(held, MatchedBy.HASH)


def test_hashes_compare_without_case_or_stray_space(tmp_path) -> None:
    held = _write(tmp_path, "whatever.pdf", REV_A)
    document = Document(
        kind="datasheet", filename="EX1234.pdf", sha256=(f" {_sha(REV_A).upper()} ",)
    )
    assert DatasheetLibrary(tmp_path).find(document) == DatasheetFile(held, MatchedBy.HASH)


def test_with_no_hash_match_the_same_name_is_taken_and_says_so(tmp_path) -> None:
    """Likely the document, maybe another revision: a weaker claim, labelled."""
    held = _write(tmp_path, "ex1234.PDF", REV_B)
    found = DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A))
    assert found == DatasheetFile(held, MatchedBy.NAME)
    assert not found.exact


def test_a_document_with_no_hashes_is_found_by_name(tmp_path) -> None:
    held = _write(tmp_path, "EX1234.pdf", REV_A)
    document = Document(kind="datasheet", filename="EX1234.pdf")
    assert DatasheetLibrary(tmp_path).find(document) == DatasheetFile(held, MatchedBy.NAME)


def test_neither_hash_nor_name_finds_nothing(tmp_path) -> None:
    _write(tmp_path, "unrelated.pdf", OTHER)
    assert DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A)) is None


def test_a_hash_match_beats_a_file_that_only_has_the_name(tmp_path) -> None:
    """The right bytes under the wrong name are the document; the name isn't."""
    _write(tmp_path, "EX1234.pdf", REV_B)
    right = _write(tmp_path, "renamed.pdf", REV_A)
    found = DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A))
    assert found == DatasheetFile(right, MatchedBy.HASH)


def test_among_same_named_files_the_documents_own_extension_wins(tmp_path) -> None:
    _write(tmp_path, "EX1234.png", OTHER)
    pdf = _write(tmp_path, "EX1234.pdf", REV_B)
    _write(tmp_path, "EX1234.txt", OTHER)
    found = DatasheetLibrary(tmp_path).find(Document(kind="datasheet", filename="ex1234.PDF"))
    assert found == DatasheetFile(pdf, MatchedBy.NAME)


def test_a_fingerprint_with_no_name_and_no_hash_finds_nothing(tmp_path) -> None:
    _write(tmp_path, "EX1234.pdf", REV_A)
    assert DatasheetLibrary(tmp_path).find(Document(kind="datasheet", filename="")) is None


# --- what gets listed ---------------------------------------------------------


def test_subfolders_are_searched(tmp_path) -> None:
    held = _write(tmp_path, "example-semi/power/EX1234.pdf", REV_A)
    assert DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A)).path == held


def test_hidden_files_and_folders_are_left_out(tmp_path) -> None:
    _write(tmp_path, ".EX1234.pdf", REV_A)
    _write(tmp_path, ".cache/EX1234.pdf", REV_A)
    library = DatasheetLibrary(tmp_path)
    assert library.files() == ()
    assert library.find(_doc("EX1234.pdf", REV_A)) is None


def test_the_listing_order_is_fixed(tmp_path) -> None:
    for name in ("b/2.pdf", "a/1.pdf", "c.pdf", "a/0.pdf"):
        _write(tmp_path, name, OTHER)
    names = [p.relative_to(tmp_path).as_posix() for p in DatasheetLibrary(tmp_path).files()]
    assert names == ["c.pdf", "a/0.pdf", "a/1.pdf", "b/2.pdf"]


def test_a_linked_folder_is_not_followed(tmp_path) -> None:
    """A link back up the tree would otherwise loop for ever."""
    library_dir = tmp_path / "library"
    _write(library_dir, "EX1234.pdf", REV_A)
    try:
        os.symlink(library_dir, library_dir / "loop", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    assert len(DatasheetLibrary(library_dir).files()) == 1


def test_a_new_file_shows_up_after_reload_not_before(tmp_path) -> None:
    library = DatasheetLibrary(tmp_path)
    assert library.find(_doc("EX1234.pdf", REV_A)) is None
    held = _write(tmp_path, "EX1234.pdf", REV_A)
    assert library.find(_doc("EX1234.pdf", REV_A)) is None  # listed once and kept
    assert library.reload() == 1
    assert library.find(_doc("EX1234.pdf", REV_A)).path == held


# --- hashing is the cost: do as little as will answer ---------------------------


def test_the_file_with_the_documents_name_is_hashed_first_and_alone(tmp_path, hashed) -> None:
    for n in range(5):
        _write(tmp_path, f"other-{n}.pdf", OTHER + bytes([n]))
    held = _write(tmp_path, "EX1234.pdf", REV_A)
    assert DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A)).exact
    assert hashed == [held]


def test_the_rest_are_hashed_only_when_the_named_file_is_not_it(tmp_path, hashed) -> None:
    named = _write(tmp_path, "EX1234.pdf", REV_B)
    right = _write(tmp_path, "zz-renamed.pdf", REV_A)
    assert DatasheetLibrary(tmp_path).find(_doc("EX1234.pdf", REV_A)).path == right
    assert hashed[0] == named
    assert set(hashed) == {named, right}


def test_a_document_with_no_hashes_hashes_nothing(tmp_path, hashed) -> None:
    _write(tmp_path, "EX1234.pdf", REV_A)
    DatasheetLibrary(tmp_path).find(Document(kind="datasheet", filename="EX1234.pdf"))
    assert hashed == []


def test_a_file_is_hashed_once(tmp_path, hashed) -> None:
    _write(tmp_path, "a.pdf", OTHER)
    _write(tmp_path, "b.pdf", REV_A)
    library = DatasheetLibrary(tmp_path)
    for _ in range(3):
        assert library.find(_doc("EX1234.pdf", REV_A)).exact
    assert len(hashed) == len(set(hashed)) == 2


def test_a_file_that_changed_is_hashed_again(tmp_path, hashed) -> None:
    held = _write(tmp_path, "EX1234.pdf", REV_B)
    library = DatasheetLibrary(tmp_path)
    assert not library.find(_doc("EX1234.pdf", REV_A)).exact
    # A new size is enough on its own, whatever the clock's grain.
    held.write_bytes(REV_A + b" ")
    assert not library.find(_doc("EX1234.pdf", REV_A)).exact
    # And so is a new time on its own: the size is back to the first one.
    held.write_bytes(REV_A)
    os.utime(held, ns=(1, 1))
    assert library.find(_doc("EX1234.pdf", REV_A)).exact
    assert hashed == [held, held, held]


def test_reload_keeps_the_hashes_of_files_that_did_not_change(tmp_path, hashed) -> None:
    _write(tmp_path, "a.pdf", OTHER)
    _write(tmp_path, "b.pdf", REV_A)
    library = DatasheetLibrary(tmp_path)
    assert library.find(_doc("EX1234.pdf", REV_A)).exact
    library.reload()
    assert library.find(_doc("EX1234.pdf", REV_A)).exact
    assert len(hashed) == 2


def test_reload_forgets_files_that_are_gone(tmp_path) -> None:
    held = _write(tmp_path, "EX1234.pdf", REV_A)
    library = DatasheetLibrary(tmp_path)
    assert library.find(_doc("EX1234.pdf", REV_A)).exact
    held.unlink()
    assert library.reload() == 0
    assert library.find(_doc("EX1234.pdf", REV_A)) is None
    assert library._hashes == {}


# --- nothing raises -----------------------------------------------------------


def test_a_file_that_cannot_be_read_is_skipped_and_said_once(tmp_path, monkeypatch, caplog) -> None:
    locked = _write(tmp_path, "a-locked.pdf", OTHER)
    right = _write(tmp_path, "b.pdf", REV_A)
    real = datasheets_module._sha256

    def refusing(path: Path) -> str:
        if path == locked:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(datasheets_module, "_sha256", refusing)
    library = DatasheetLibrary(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert library.find(_doc("EX1234.pdf", REV_A)).path == right
        assert library.find(_doc("ZZ9999.pdf", b"not held")) is None  # hashes the lot again?
    assert len([r for r in caplog.records if "a-locked.pdf" in r.message]) == 1


def test_a_file_deleted_after_listing_is_skipped(tmp_path) -> None:
    gone = _write(tmp_path, "a.pdf", OTHER)
    right = _write(tmp_path, "b.pdf", REV_A)
    library = DatasheetLibrary(tmp_path)
    library.files()
    gone.unlink()
    assert library.find(_doc("EX1234.pdf", REV_A)).path == right


def test_a_listing_that_blows_up_finds_nothing(tmp_path, monkeypatch) -> None:
    def explode(_root: Path) -> tuple[Path, ...]:
        raise RuntimeError("unplanned")

    monkeypatch.setattr(datasheets_module, "_list", explode)
    library = DatasheetLibrary(tmp_path)
    assert library.files() == ()
    assert library.find(_doc("EX1234.pdf", REV_A)) is None


def test_a_malformed_fingerprint_finds_nothing_instead_of_raising(tmp_path) -> None:
    _write(tmp_path, "EX1234.pdf", REV_A)
    broken = Document(kind="datasheet", filename=None, sha256=(None, 7))  # type: ignore[arg-type]
    assert DatasheetLibrary(tmp_path).find(broken) is None


def test_lookups_from_several_threads_agree(tmp_path) -> None:
    for n in range(8):
        _write(tmp_path, f"other-{n}.pdf", OTHER + bytes([n]))
    right = _write(tmp_path, "renamed.pdf", REV_A)
    library = DatasheetLibrary(tmp_path)
    answers: list[Path | None] = []

    def look() -> None:
        found = library.find(_doc("EX1234.pdf", REV_A))
        answers.append(found.path if found else None)

    threads = [threading.Thread(target=look) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert answers == [right] * 6


# --- a chip's documents -------------------------------------------------------


def _chip(*documents: Document) -> Chip:
    return Chip(mpn="EX1234QFN", manufacturer="Example Semi", documents=documents)


def test_a_chips_documents_come_back_in_its_own_order(tmp_path) -> None:
    sheet = _write(tmp_path, "EX1234.pdf", REV_A)
    note = _write(tmp_path, "AN-0042.pdf", OTHER)
    documents = (
        _doc("EX1234.pdf", REV_A),
        _doc("EX1234-ref-design.pdf", b"not held", kind="example-circuit"),
        Document(kind="app-note", filename="an-0042.pdf"),
    )
    assert DatasheetLibrary(tmp_path).for_chip(_chip(*documents)) == (
        ChipDocument(documents[0], DatasheetFile(sheet, MatchedBy.HASH)),
        ChipDocument(documents[1], None),
        ChipDocument(documents[2], DatasheetFile(note, MatchedBy.NAME)),
    )


def test_a_chip_with_no_documents_has_none(tmp_path) -> None:
    library = DatasheetLibrary(tmp_path)
    assert library.for_chip(_chip()) == ()
    assert library.datasheet(_chip()) is None


def test_open_datasheet_takes_the_datasheet_not_the_app_note(tmp_path) -> None:
    _write(tmp_path, "AN-0042.pdf", OTHER)
    sheet = _write(tmp_path, "EX1234.pdf", REV_A)
    chip = _chip(_doc("AN-0042.pdf", OTHER, kind="app-note"), _doc("EX1234.pdf", REV_A))
    assert DatasheetLibrary(tmp_path).datasheet(chip) == DatasheetFile(sheet, MatchedBy.HASH)


def test_open_datasheet_prefers_the_exact_revision(tmp_path) -> None:
    """An earlier document found only by name loses to a later one found by hash."""
    _write(tmp_path, "EX1234-old.pdf", OTHER)
    exact = _write(tmp_path, "EX1234.pdf", REV_A)
    chip = _chip(_doc("EX1234-old.pdf", b"some other bytes"), _doc("EX1234.pdf", REV_A))
    assert DatasheetLibrary(tmp_path).datasheet(chip) == DatasheetFile(exact, MatchedBy.HASH)


def test_open_datasheet_has_nothing_when_only_other_kinds_are_held(tmp_path) -> None:
    _write(tmp_path, "AN-0042.pdf", OTHER)
    chip = _chip(_doc("AN-0042.pdf", OTHER, kind="app-note"), _doc("EX1234.pdf", REV_A))
    assert DatasheetLibrary(tmp_path).datasheet(chip) is None


# --- opening ------------------------------------------------------------------


def test_opening_hands_the_path_to_the_os_opener(tmp_path) -> None:
    held = _write(tmp_path, "EX1234.pdf", REV_A)
    opened: list[Path] = []
    assert open_document(held, os_open=opened.append) is True
    assert opened == [held]


def test_opening_a_file_that_is_gone_opens_nothing(tmp_path) -> None:
    opened: list[Path] = []
    assert open_document(tmp_path / "gone.pdf", os_open=opened.append) is False
    assert opened == []


def test_an_opener_that_fails_is_reported_not_raised(tmp_path) -> None:
    held = _write(tmp_path, "EX1234.pdf", REV_A)

    def broken(_path: Path) -> object:
        raise OSError("no handler for .pdf")

    assert open_document(held, os_open=broken) is False


def test_the_default_opener_is_the_one_boards_use() -> None:
    """One OS-open rule in the app, not two that drift."""
    from benchhud.boards import opener

    assert datasheets_module.default_os_open is opener.default_os_open


def test_the_library_needs_no_qt() -> None:
    import subprocess
    import sys

    code = (
        "import sys, benchhud.chips.datasheets as d; "
        "assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt imported'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
