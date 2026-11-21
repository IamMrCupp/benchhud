"""Tests for the directory chip source: a built index, and a bare checkout.

Pure filesystem work against ``tmp_path``: no Qt, no network, no git. Every
chip here is made up — "Example Semi" and "Sample Devices" don't exist and
neither do their parts — and so is the little vocabulary beside them.

``tests/fixtures/chip-data/index.json`` is a real build: chip-data's own
builder wrote it from the tree :func:`write_checkout` makes, so the reader is
held to what the builder actually emits. The tests never import ``chipdata``.
To regenerate it after changing the tree below::

    uv run python tests/test_chip_dir_source.py /tmp/made-up-chips
    (cd ~/Code/chip-data && uv run chipdata build --root /tmp/made-up-chips \
        --out /tmp/made-up-chips/out)
    cp /tmp/made-up-chips/out/index.json tests/fixtures/chip-data/index.json
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

import pytest

from benchhud.chips import (
    Chip,
    ChipSource,
    DirChipSource,
    Document,
    InMemoryChipSource,
    Marking,
    Substitute,
    UsedIn,
)
from benchhud.chips import dir_source as dir_source_module

FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

_FUNCTIONS_TOML = """\
[functions]
buck-controller = "Step-down controller that drives outside switches"
ldo = "Linear regulator"
load-switch = "Turns a rail on and off"
other = "None of the above"

[deprecated]
standby-buck = "buck-controller"
low-dropout = "ldo"
"""

# "SOT" before "SOT-23" on purpose: the longer family has to win.
_PACKAGES_TOML = """\
families = ["BGA", "QFN", "SOT", "SOT-23"]
"""

_CHIP_FILES = {
    "example-semi/EX1234QFN.toml": """\
schema = 1
mpn = "EX1234QFN"
manufacturer = "Example Semi"
family = "EX1234"
package = "QFN-20 (3x3)"
functions = ["standby-buck"]
description = "Single-phase buck controller for a standby rail"
sources = ["bench-notes"]

[[markings]]
code = "X1="
examples = ["X1=6A", "X1=7C"]
note = "Top line. The second line is a date code."

[[documents]]
kind = "datasheet"
filename = "EX1234.pdf"
pages = 14
sha256 = ["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"]
url = "https://example.com/ex1234"

[[substitutes]]
mpn = "EX1235QFN"
note = "Pin-compatible; check the enable threshold"

[[used_in]]
board = "EXAMPLE-BOARD-1"
designator = "U7000"
role = "standby 3.3 V"
""",
    "example-semi/EX9000SOT.toml": """\
schema = 1
mpn = "EX9000SOT"
manufacturer = "Example Semi"
package = "SOT-23-5"
functions = ["low-dropout", "ldo", "load-switch"]

[[markings]]
code = "X1="

[[markings]]
code = "X9"
examples = ["X9B"]
""",
    "sample-devices/SD400BGA.toml": """\
schema = 1
mpn = "SD400BGA"
manufacturer = "Sample Devices"
package = "BGA"
functions = ["other"]

[[markings]]
code = "SD4"

[[documents]]
kind = "app-note"
filename = "SD400-layout.pdf"
note = "No public source known"

[[used_in]]
board = "EXAMPLE-BOARD-2"
""",
    # A "/" can't be in a filename, so chip-data writes it as "_".
    "sample-devices/SD77_NOPB.toml": """\
schema = 1
mpn = "SD77/NOPB"
manufacturer = "Sample Devices"
""",
}

_ALL = ["EX1234QFN", "EX9000SOT", "SD400BGA", "SD77/NOPB"]

_BUCK = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    family="EX1234",
    package="QFN-20 (3x3)",
    package_family="QFN",
    functions=("buck-controller",),
    description="Single-phase buck controller for a standby rail",
    sources=("bench-notes",),
    markings=(
        Marking(
            code="X1=",
            examples=("X1=6A", "X1=7C"),
            note="Top line. The second line is a date code.",
        ),
    ),
    documents=(
        Document(
            kind="datasheet",
            filename="EX1234.pdf",
            pages=14,
            sha256=("0123456789abcdef" * 4,),
            url="https://example.com/ex1234",
        ),
    ),
    substitutes=(Substitute(mpn="EX1235QFN", note="Pin-compatible; check the enable threshold"),),
    used_in=(UsedIn(board="EXAMPLE-BOARD-1", designator="U7000", role="standby 3.3 V"),),
)


def write_checkout(root: Path, *, vocab: bool = True) -> Path:
    """The made-up tree, in the shape of a chip-data checkout."""
    for name, text in _CHIP_FILES.items():
        path = root / "chips" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if vocab:
        (root / "vocab").mkdir(parents=True, exist_ok=True)
        (root / "vocab" / "functions.toml").write_text(_FUNCTIONS_TOML, encoding="utf-8")
        (root / "vocab" / "packages.toml").write_text(_PACKAGES_TOML, encoding="utf-8")
    return root


def _write_index(path: Path, chips: list, **top) -> Path:
    """A hand-made index, for the shapes the builder would never write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    index = {"format": 1, "digest": "0" * 64, "count": len(chips), "functions": {}, "chips": chips}
    index.update(top)
    path.write_text(json.dumps(index), encoding="utf-8")
    return path


def _record(mpn: str, **fields) -> dict:
    return {"mpn": mpn, "manufacturer": "Example Semi", **fields}


def _set_times(root: Path, when: float) -> None:
    """Give everything under ``root`` (and ``root``) one modification time."""
    for path in (root, *root.rglob("*")):
        os.utime(path, (when, when))


def _chips(source: DirChipSource) -> dict[str, Chip]:
    return {mpn: chip for mpn in _ALL if (chip := source.get(mpn)) is not None}


@pytest.fixture
def warned(caplog):
    """Call it for the warning lines the reader has logged so far."""
    caplog.set_level(logging.INFO, logger="benchhud.chips.dir_source")

    def lines() -> list[str]:
        return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]

    return lines


# --- the built index ------------------------------------------------------


def test_the_fixture_is_a_build_of_the_tree_in_this_file() -> None:
    index = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert index["format"] == 1
    assert [record["mpn"] for record in index["chips"]] == _ALL
    assert set(index["functions"]) == {"buck-controller", "ldo", "load-switch", "other"}


def test_reads_an_index_the_builder_wrote(warned) -> None:
    source = DirChipSource(root=FIXTURE)
    assert source.reload() == 4
    assert source.origin == FIXTURE
    assert source.get("EX1234QFN") == _BUCK
    assert warned() == []


def test_an_index_record_with_nothing_optional_comes_back_empty() -> None:
    bare = DirChipSource(root=FIXTURE).get("SD77/NOPB")
    assert bare == Chip(mpn="SD77/NOPB", manufacturer="Sample Devices")


@pytest.mark.parametrize("where", ["index.json", "out/index.json"])
def test_finds_the_index_beside_the_files_or_in_out(tmp_path, where: str) -> None:
    index = tmp_path / where
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_bytes(FIXTURE.read_bytes())
    source = DirChipSource(root=tmp_path)
    assert source.get("ex1234qfn") == _BUCK
    assert source.origin == index


def test_lookups_from_an_index() -> None:
    source = DirChipSource(root=FIXTURE)
    assert source.get(" ex9000sot ").mpn == "EX9000SOT"
    assert source.get("EX1234") is None
    assert source.get("") is None
    found = source.by_marking("X1=6A")
    assert [m.chip.mpn for m in found] == ["EX1234QFN", "EX9000SOT"]
    assert [m.chip.package_family for m in found] == ["QFN", "SOT-23"]
    assert [m.chip.functions for m in found] == [("buck-controller",), ("ldo", "load-switch")]
    assert source.by_marking("") == []


def test_keys_it_does_not_know_are_ignored(tmp_path, warned) -> None:
    record = _record(
        "EX1234QFN",
        pinout={"1": "VIN"},
        markings=[{"code": "X1=", "examples": [], "note": None, "ink": "laser"}],
        documents=[{"kind": "datasheet", "filename": "EX1234.pdf", "revision": "C"}],
    )
    index = _write_index(tmp_path / "index.json", [record], generator="someone-else")
    chip = DirChipSource(root=index).get("EX1234QFN")
    assert chip.markings == (Marking(code="X1="),)
    assert chip.documents == (Document(kind="datasheet", filename="EX1234.pdf"),)
    assert warned() == []


def test_a_function_or_document_kind_it_has_never_seen_is_carried(tmp_path, warned) -> None:
    record = _record(
        "EX1234QFN",
        functions=["quantum-flux-gate"],
        documents=[{"kind": "errata", "filename": "EX1234-errata.pdf"}],
    )
    chip = DirChipSource(root=_write_index(tmp_path / "index.json", [record])).get("EX1234QFN")
    assert chip.functions == ("quantum-flux-gate",)
    assert chip.documents[0].kind == "errata"
    assert warned() == []


def test_a_newer_format_is_not_guessed_at(tmp_path, warned) -> None:
    _write_index(tmp_path / "index.json", [_record("EX1234QFN")], format=2)
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 0
    assert source.get("EX1234QFN") is None
    assert source.by_marking("X1=") == []
    assert source.origin is None
    (line,) = warned()
    assert "format 2" in line
    assert "reads format 1" in line


@pytest.mark.parametrize("version", [None, "1", True, 0, 1.0])
def test_an_index_without_a_usable_format_number_is_not_read(tmp_path, warned, version) -> None:
    _write_index(tmp_path / "index.json", [_record("EX1234QFN")], format=version)
    assert DirChipSource(root=tmp_path).reload() == 0
    assert len(warned()) == 1


@pytest.mark.parametrize(
    "text",
    ["{not json", "", "[]", '"index"', '{"format": 1}', '{"format": 1, "chips": {"a": 1}}'],
)
def test_an_index_that_is_not_an_index_loads_nothing(tmp_path, warned, text: str) -> None:
    (tmp_path / "index.json").write_text(text, encoding="utf-8")
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 0
    assert source.get("EX1234QFN") is None
    assert len(warned()) == 1


def test_an_index_that_is_not_text_loads_nothing(tmp_path, warned) -> None:
    (tmp_path / "index.json").write_bytes(b"\xff\xfe\x00\x01 not utf-8")
    assert DirChipSource(root=tmp_path).reload() == 0
    assert len(warned()) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"manufacturer": "Example Semi"},  # no part number
        {"mpn": "   ", "manufacturer": "Example Semi"},
        {"mpn": "EXBAD"},  # no maker
        {"mpn": 1234, "manufacturer": "Example Semi"},
        _record("EXBAD", functions="buck-controller"),  # text where a list belongs
        _record("EXBAD", functions=[1, 2]),
        _record("EXBAD", package=20),
        _record("EXBAD", markings=[{"examples": ["X1=6A"]}]),  # a marking with no code
        _record("EXBAD", markings=["X1="]),
        _record("EXBAD", documents=[{"kind": "datasheet", "filename": "a.pdf", "pages": "14"}]),
        _record("EXBAD", documents=[{"kind": "datasheet", "filename": "a.pdf", "pages": True}]),
        _record("EXBAD", substitutes=[{"note": "no part number"}]),
        _record("EXBAD", used_in=[{"designator": "U1"}]),
        "EXBAD",
        None,
    ],
)
def test_one_bad_chip_does_not_take_the_good_ones_down(tmp_path, warned, bad) -> None:
    records = [_record("EX1000QFN"), bad, _record("EX2000QFN")]
    source = DirChipSource(root=_write_index(tmp_path / "index.json", records))
    assert source.reload() == 2
    assert source.get("EX1000QFN") is not None
    assert source.get("EX2000QFN") is not None
    assert source.get("EXBAD") is None
    assert len(warned()) == 1


def test_a_part_number_twice_in_an_index_is_the_first_one(tmp_path, warned) -> None:
    records = [
        _record("EX1234QFN", package="QFN-20 (3x3)", markings=[{"code": "X1"}]),
        _record("ex1234qfn", package="SOP-8", markings=[{"code": "X1="}]),
    ]
    source = DirChipSource(root=_write_index(tmp_path / "index.json", records))
    assert source.reload() == 1
    assert source.get("ex1234qfn").package == "QFN-20 (3x3)"
    (match,) = source.by_marking("X1=6A")
    assert match.chip.package == "QFN-20 (3x3)"
    same_list = InMemoryChipSource([dir_source_module.chip_from_mapping(r) for r in records])
    assert same_list.get("ex1234qfn") == source.get("ex1234qfn")
    assert same_list.by_marking("X1=6A") == source.by_marking("X1=6A")
    assert len(warned()) == 1


# --- chip files -----------------------------------------------------------


def test_reads_a_bare_checkout(tmp_path, warned) -> None:
    source = DirChipSource(root=write_checkout(tmp_path))
    assert source.reload() == 4
    assert source.origin == tmp_path / "chips"
    assert source.get("ex1234qfn") == _BUCK
    assert warned() == []


def test_both_paths_give_the_same_chips(tmp_path) -> None:
    from_files = _chips(DirChipSource(root=write_checkout(tmp_path)))
    from_index = _chips(DirChipSource(root=FIXTURE))
    assert sorted(from_files) == sorted(_ALL)
    assert from_files == from_index


def test_both_paths_answer_a_marking_the_same(tmp_path) -> None:
    files = DirChipSource(root=write_checkout(tmp_path))
    index = DirChipSource(root=FIXTURE)
    for typed in ("X1=6A", "x1", "X9B", "SD4", "nope"):
        assert files.by_marking(typed) == index.by_marking(typed)


def test_the_part_number_comes_from_the_file_not_its_name(tmp_path) -> None:
    source = DirChipSource(root=write_checkout(tmp_path))
    assert source.get("SD77/NOPB") == Chip(mpn="SD77/NOPB", manufacturer="Sample Devices")
    assert source.get("SD77_NOPB") is None


def test_package_family_is_the_longest_family_the_package_starts_with(tmp_path) -> None:
    source = DirChipSource(root=write_checkout(tmp_path))
    assert source.get("EX9000SOT").package_family == "SOT-23"  # not "SOT"
    assert source.get("SD400BGA").package_family == "BGA"  # the family alone
    assert source.get("EX1234QFN").package_family == "QFN"
    assert source.get("SD77/NOPB").package_family is None  # no package


def test_a_package_no_family_fits_has_no_family(tmp_path) -> None:
    write_checkout(tmp_path)
    for mpn, package in (("EX1", "QFNX-20"), ("EX2", "TO-220"), ("EX3", "qfn-20")):
        (tmp_path / "chips" / f"{mpn}.toml").write_text(
            f'schema = 1\nmpn = "{mpn}"\nmanufacturer = "Example Semi"\npackage = "{package}"\n'
        )
    source = DirChipSource(root=tmp_path)
    assert [source.get(mpn).package_family for mpn in ("EX1", "EX2", "EX3")] == [None, None, None]
    assert source.get("EX1").package == "QFNX-20"


def test_a_replaced_function_slug_is_read_as_its_successor(tmp_path) -> None:
    source = DirChipSource(root=write_checkout(tmp_path))
    assert source.get("EX1234QFN").functions == ("buck-controller",)
    # "low-dropout" becomes "ldo", which is already there: once, in place.
    assert source.get("EX9000SOT").functions == ("ldo", "load-switch")


def test_an_overlay_with_no_vocab_still_loads(tmp_path, warned) -> None:
    source = DirChipSource(root=write_checkout(tmp_path, vocab=False))
    assert source.reload() == 4
    chip = source.get("EX9000SOT")
    assert chip.package == "SOT-23-5"
    assert chip.package_family is None
    assert chip.functions == ("low-dropout", "ldo", "load-switch")
    assert warned() == []


@pytest.mark.parametrize(
    ("packages", "functions"),
    [
        ("families = [", "[deprecated"),  # not TOML
        ('families = "QFN"', 'deprecated = "none"'),  # the wrong shape
        ("families = [1, 2]", "[deprecated]\nstandby-buck = 7"),
    ],
)
def test_a_broken_vocab_is_the_same_as_none(tmp_path, packages: str, functions: str) -> None:
    write_checkout(tmp_path)
    (tmp_path / "vocab" / "packages.toml").write_text(packages)
    (tmp_path / "vocab" / "functions.toml").write_text(functions)
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.get("EX1234QFN").package_family is None
    assert source.get("EX1234QFN").functions == ("standby-buck",)


@pytest.mark.parametrize(
    "text",
    [
        "mpn = [unclosed",  # not TOML
        'mpn = "EXBAD"\nmanufacturer = "Example Semi"\n',  # no schema
        'schema = 2\nmpn = "EXBAD"\nmanufacturer = "Example Semi"\n',  # a newer schema
        'schema = "1"\nmpn = "EXBAD"\nmanufacturer = "Example Semi"\n',
        'schema = 1\nmanufacturer = "Example Semi"\n',  # no part number
        'schema = 1\nmpn = "EXBAD"\n',  # no maker
        'schema = 1\nmpn = "EXBAD"\nmanufacturer = "Example Semi"\nfunctions = "ldo"\n',
        'schema = 1\nmpn = "EXBAD"\nmanufacturer = "Example Semi"\n[[markings]]\nnote = "x"\n',
        "",
    ],
)
def test_one_bad_chip_file_does_not_take_the_good_ones_down(tmp_path, warned, text: str) -> None:
    write_checkout(tmp_path)
    (tmp_path / "chips" / "example-semi" / "EXBAD.toml").write_text(text)
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.get("EXBAD") is None
    assert source.get("EX1234QFN") == _BUCK
    (line,) = warned()
    assert "EXBAD.toml" in line


def test_a_chip_file_that_is_not_text_is_skipped(tmp_path, warned) -> None:
    write_checkout(tmp_path)
    (tmp_path / "chips" / "example-semi" / "EXBAD.toml").write_bytes(b"\xff\xfe\x00 nope")
    assert DirChipSource(root=tmp_path).reload() == 4
    assert len(warned()) == 1


def test_only_toml_files_under_chips_are_chips(tmp_path, warned) -> None:
    write_checkout(tmp_path)
    (tmp_path / "chips" / "README.md").write_text("# chips\n")
    (tmp_path / "chips" / "example-semi" / "notes.txt").write_text('mpn = "EXNOTE"\n')
    (tmp_path / "chips" / "folder.toml").mkdir()
    (tmp_path / "stray.toml").write_text('schema = 1\nmpn = "EXSTRAY"\nmanufacturer = "x"\n')
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.get("EXSTRAY") is None
    assert warned() == []


def test_a_part_number_in_two_files_is_the_first_in_path_order(tmp_path, warned) -> None:
    write_checkout(tmp_path)
    again = tmp_path / "chips" / "zzz-other" / "ex1234qfn.toml"
    again.parent.mkdir()
    again.write_text('schema = 1\nmpn = "ex1234qfn"\nmanufacturer = "Zzz Other"\n')
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.get("ex1234qfn") == _BUCK
    assert source.by_marking("X1=")[0].chip.manufacturer == "Example Semi"
    (line,) = warned()
    assert "twice" in line

    # The other way round: the earlier path wins, whichever file is "better".
    first = tmp_path / "chips" / "aaa-other" / "EX1234QFN.toml"
    first.parent.mkdir()
    first.write_text('schema = 1\nmpn = "EX1234QFN"\nmanufacturer = "Aaa Other"\n')
    source.reload()
    assert source.get("EX1234QFN").manufacturer == "Aaa Other"
    assert source.by_marking("X1=6A")[0].chip.mpn == "EX9000SOT"


# --- which one gets read --------------------------------------------------


def _checkout_with_index(root: Path, *, index_age: float) -> Path:
    """A checkout whose chip files are from time 1000 and whose index is
    ``index_age`` seconds newer (or older, when negative)."""
    write_checkout(root)
    index = root / "out" / "index.json"
    index.parent.mkdir()
    index.write_bytes(FIXTURE.read_bytes())
    _set_times(root, 1000.0)
    os.utime(index, (1000.0 + index_age, 1000.0 + index_age))
    return index


@pytest.mark.parametrize("age", [0.0, 60.0])
def test_an_index_as_new_as_the_chip_files_is_what_gets_read(tmp_path, warned, age) -> None:
    index = _checkout_with_index(tmp_path, index_age=age)
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.origin == index
    assert warned() == []


def test_a_chip_file_newer_than_the_index_means_the_files_are_read(tmp_path, warned) -> None:
    _checkout_with_index(tmp_path, index_age=60.0)
    edited = tmp_path / "chips" / "example-semi" / "EX9000SOT.toml"
    edited.write_text(edited.read_text().replace("SOT-23-5", "SOT-23-6"))
    os.utime(edited, (2000.0, 2000.0))
    source = DirChipSource(root=tmp_path)
    assert source.get("EX9000SOT").package == "SOT-23-6"  # the index still says -5
    assert source.origin == tmp_path / "chips"
    (line,) = warned()
    assert "older than the chip files" in line


def test_a_deleted_chip_file_is_noticed_too(tmp_path) -> None:
    _checkout_with_index(tmp_path, index_age=60.0)
    folder = tmp_path / "chips" / "sample-devices"
    (folder / "SD400BGA.toml").unlink()
    os.utime(folder, (2000.0, 2000.0))  # removing an entry is what touches a folder
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 3
    assert source.get("SD400BGA") is None


def test_a_changed_vocab_file_makes_the_index_old_as_well(tmp_path) -> None:
    _checkout_with_index(tmp_path, index_age=60.0)
    packages = tmp_path / "vocab" / "packages.toml"
    packages.write_text('families = ["BGA", "QFN", "SOT"]\n')
    os.utime(packages, (2000.0, 2000.0))
    source = DirChipSource(root=tmp_path)
    assert source.get("EX9000SOT").package_family == "SOT"
    assert source.origin == tmp_path / "chips"


def test_an_unpacked_build_has_nothing_to_be_older_than(tmp_path, warned) -> None:
    index = tmp_path / "index.json"
    index.write_bytes(FIXTURE.read_bytes())
    os.utime(index, (1.0, 1.0))
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.origin == index
    assert warned() == []


def test_a_newer_format_beside_chip_files_falls_back_to_the_files(tmp_path, warned) -> None:
    write_checkout(tmp_path)
    _write_index(tmp_path / "out" / "index.json", [_record("EXFUTURE")], format=2)
    _set_times(tmp_path, 1000.0)
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.get("EXFUTURE") is None
    assert source.origin == tmp_path / "chips"
    (line,) = warned()
    assert "format 2" in line


def test_a_broken_index_beside_chip_files_falls_back_to_the_files(tmp_path, warned) -> None:
    write_checkout(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "index.json").write_text("{half an index")
    _set_times(tmp_path, 1000.0)
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    assert source.origin == tmp_path / "chips"
    assert len(warned()) == 1


# --- when it's read, and when there's nothing to read ---------------------


def test_it_is_a_chip_source() -> None:
    assert isinstance(DirChipSource(), ChipSource)
    assert DirChipSource().name == "chip-data"
    assert DirChipSource(name="community").name == "community"


def test_not_configured_finds_nothing_and_says_nothing(warned) -> None:
    for source in (DirChipSource(), DirChipSource.from_config(""), DirChipSource.from_config("  ")):
        assert source.root is None
        assert source.reload() == 0
        assert source.get("EX1234QFN") is None
        assert source.by_marking("X1=") == []
        assert source.origin is None
    assert warned() == []


def test_from_config_takes_a_path_and_a_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    source = DirChipSource.from_config("~/chips-private", name="local")
    assert source.root == tmp_path / "chips-private"
    assert source.name == "local"


def test_a_directory_that_is_not_there_finds_nothing(tmp_path, warned) -> None:
    source = DirChipSource(root=tmp_path / "nowhere", name="community")
    assert source.reload() == 0
    assert source.get("EX1234QFN") is None
    assert source.by_marking("X1=") == []
    (line,) = warned()
    assert "community" in line


def test_a_directory_with_no_chip_data_in_it_finds_nothing(tmp_path, warned) -> None:
    (tmp_path / "notes.txt").write_text("not a chip")
    assert DirChipSource(root=tmp_path).reload() == 0
    assert len(warned()) == 1


def test_an_empty_checkout_is_not_a_problem(tmp_path, warned) -> None:
    (tmp_path / "chips").mkdir()
    (tmp_path / "chips" / "README.md").write_text("# chips\n")
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 0
    assert source.origin == tmp_path / "chips"
    assert warned() == []


def test_nothing_is_read_until_the_first_lookup(tmp_path) -> None:
    source = DirChipSource(root=tmp_path)  # nothing there yet
    write_checkout(tmp_path)
    assert source.get("EX1234QFN") == _BUCK


def test_a_changed_directory_is_picked_up_by_reload_and_not_before(tmp_path) -> None:
    write_checkout(tmp_path)
    source = DirChipSource(root=tmp_path)
    assert source.get("EXNEW") is None
    (tmp_path / "chips" / "example-semi" / "EXNEW.toml").write_text(
        'schema = 1\nmpn = "EXNEW"\nmanufacturer = "Example Semi"\n[[markings]]\ncode = "N7"\n'
    )
    assert source.get("EXNEW") is None
    assert source.by_marking("N7") == []
    assert source.reload() == 5
    assert source.get("EXNEW").manufacturer == "Example Semi"
    assert [m.chip.mpn for m in source.by_marking("N7")] == ["EXNEW"]


def test_a_directory_that_goes_away_reloads_to_nothing(tmp_path) -> None:
    index = tmp_path / "index.json"
    index.write_bytes(FIXTURE.read_bytes())
    source = DirChipSource(root=tmp_path)
    assert source.reload() == 4
    index.unlink()
    assert source.get("EX1234QFN") == _BUCK  # still the copy in memory
    assert source.reload() == 0
    assert source.get("EX1234QFN") is None


def test_lookups_arriving_together_read_the_directory_once(tmp_path, monkeypatch) -> None:
    write_checkout(tmp_path)
    real_load = dir_source_module._load
    reads: list[int] = []
    everyone_waiting = threading.Barrier(6)

    def slow_load(root, name):
        reads.append(1)
        return real_load(root, name)

    monkeypatch.setattr(dir_source_module, "_load", slow_load)
    source = DirChipSource(root=tmp_path)
    found: list[Chip | None] = []

    def look_up() -> None:
        everyone_waiting.wait(timeout=10)
        found.append(source.get("EX1234QFN"))

    threads = [threading.Thread(target=look_up) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert found == [_BUCK] * 6
    assert len(reads) == 1


def test_a_lookup_during_a_reload_answers_from_the_copy_already_held(tmp_path, monkeypatch) -> None:
    write_checkout(tmp_path)
    source = DirChipSource(root=tmp_path)
    assert source.get("EX1234QFN") == _BUCK

    real_load = dir_source_module._load
    reading = threading.Event()
    carry_on = threading.Event()

    def held_load(root, name):
        reading.set()
        assert carry_on.wait(timeout=10)
        return real_load(root, name)

    monkeypatch.setattr(dir_source_module, "_load", held_load)
    (tmp_path / "chips" / "example-semi" / "EX1234QFN.toml").unlink()
    counted: list[int] = []
    reloader = threading.Thread(target=lambda: counted.append(source.reload()))
    reloader.start()
    assert reading.wait(timeout=10)
    try:
        assert source.get("EX1234QFN") == _BUCK  # mid-reload: the old copy, no waiting
        assert len(source.by_marking("X1=")) == 2
    finally:
        carry_on.set()
        reloader.join(timeout=10)
    assert counted == [3]
    assert source.get("EX1234QFN") is None


if __name__ == "__main__":
    print(write_checkout(Path(sys.argv[1])))
