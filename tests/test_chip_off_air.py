"""A chip source kept off air (#482).

``on_air = false`` on a ``[[chips.sources]]`` entry keeps that source out of
what the Chip panel shows — the panel is stream output — while Find chip and
the inventory row still use it. The things pinned here: the panel is handed
the on-air sources and nothing else; an off-air source's values never reach
the card, whichever way a chip gets there; the card gives no sign anything is
held back; and a switch flipped in Preferences takes hold on OK.

Offscreen, no network; nothing opens a real file or browser. Every chip, part
and board is made up — "Example Semi" doesn't exist.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QWidget

from benchhud import config as config_module
from benchhud.chips import CompositeChipSource, NullChipSource, builder
from benchhud.chips import datasheets as datasheets_module
from benchhud.chips.builder import ChipSources, build_chip_source, build_chip_sources
from benchhud.chips.card import SHOW_ON_CARD_TIP, sent_line
from benchhud.chips.dir_source import FROM_FILES, FROM_INDEX, LoadReport
from benchhud.config import CHIP_SOURCE_ON_AIR, ChipSourceConfig, Config
from benchhud.inventory import InMemoryInventorySource, Part
from benchhud.shell import chip_dialog
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels import chip as chip_panel_module
from benchhud.shell.panels.chip import State
from benchhud.shell.preferences import NO_SOURCE_ON_AIR, PreferencesDialog

REPO = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

NAME, FOLDER, ON, ON_AIR, LOADED = range(5)

#: The community build's words for EX1234QFN (the fixture).
_PUBLIC_PACKAGE = "QFN-20 (3x3)"
_PUBLIC_DESCRIPTION = "Single-phase buck controller for a standby rail"

#: Everything the private overlay says that the community build doesn't. None
#: of it may be drawn by the card while the overlay is off air.
_PRIVATE = (
    "QFN-24",
    "grey donor",
    "runs hot",
    "ZZ7",
    "blue scrap",
    "Hush Devices",
    "PRIV77SOT",
    "priv77sot",
    "SC-70",
    "P7",
)
#: Wording that would tell a viewer something is being held back.
_HINTS = ("off air", "on air", "held back", "private", "overlay", "hidden", "more", "source")


class _FakeScanner:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


@pytest.fixture(autouse=True)
def nothing_leaves(monkeypatch):
    """No real browser, no real file opened."""
    reached: list[object] = []
    monkeypatch.setattr(chip_dialog, "open_in_browser", lambda url: reached.append(url) or False)
    monkeypatch.setattr(datasheets_module, "default_os_open", reached.append)
    yield
    assert reached == []


@pytest.fixture
def worker_jobs(monkeypatch) -> list:
    """Jobs handed to the chips worker thread, held instead of run."""
    jobs: list = []
    monkeypatch.setattr(builder, "_thread", jobs.append)
    return jobs


def _settle(worker_jobs: list) -> None:
    while worker_jobs:
        worker_jobs.pop()()


def _inline(job) -> None:
    job()


def _build_dir(tmp_path: Path, name: str = "build") -> Path:
    build = tmp_path / name
    build.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, build / "index.json")
    return build


def _overlay(tmp_path: Path, name: str = "chips-private") -> Path:
    """A private overlay: its own words for a chip the build has, and a chip only it has."""
    folder = tmp_path / name / "chips" / "example-semi"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "EX1234QFN.toml").write_text(
        'schema = 1\nmpn = "EX1234QFN"\nmanufacturer = "Example Semi"\n'
        'package = "QFN-24 (4x4)"\n'
        'description = "From the grey donor board, runs hot"\n'
        '[[markings]]\ncode = "ZZ7"\n',
        encoding="utf-8",
    )
    (folder / "PRIV77SOT.toml").write_text(
        'schema = 1\nmpn = "PRIV77SOT"\nmanufacturer = "Hush Devices"\n'
        'package = "SC-70"\n'
        'description = "Pulled from the blue scrap board"\n'
        '[[markings]]\ncode = "P7"\n',
        encoding="utf-8",
    )
    return tmp_path / name


def _everything_drawn(widget: QWidget) -> str:
    """Every piece of text the widget could put on screen, in one string."""
    bits: list[str] = []
    for child in [widget, *widget.findChildren(QWidget)]:
        bits += [child.toolTip(), child.statusTip(), child.whatsThis()]
        bits += [child.accessibleName(), child.accessibleDescription(), child.windowTitle()]
        if isinstance(child, QLabel | QAbstractButton):
            bits.append(child.text())
        if isinstance(child, QLineEdit):
            bits += [child.text(), child.placeholderText()]
    return "\n".join(bit for bit in bits if bit)


def _nothing_private(panel: QWidget) -> None:
    drawn = _everything_drawn(panel)
    for text in _PRIVATE:
        assert text not in drawn, text


def _sources(tmp_path: Path, *, overlay_on_air: bool, community_on_air: bool = True) -> list:
    """The overlay first — its values win — then the community build."""
    return [
        ChipSourceConfig("local", path=str(_overlay(tmp_path)), on_air=overlay_on_air),
        ChipSourceConfig("community", path=str(_build_dir(tmp_path)), on_air=community_on_air),
    ]


def _window(qtbot, tmp_path, worker_jobs, **on_air) -> MainWindow:
    on_air.setdefault("overlay_on_air", False)
    config = Config()
    config.chips.sources = _sources(tmp_path, **on_air)
    window = MainWindow(config, scanner=_FakeScanner(), on_persist=lambda c: None)
    qtbot.addWidget(window)
    window.show()
    _settle(worker_jobs)
    window.chip_panel._executor = _inline
    return window


# --- the config key --------------------------------------------------------


def test_the_default_is_on_air_and_lives_in_one_place() -> None:
    """The issue's stated default. Whether a source that isn't a chip-data
    checkout should default the other way is still open — it's this constant."""
    assert CHIP_SOURCE_ON_AIR is True
    assert ChipSourceConfig("community").on_air is CHIP_SOURCE_ON_AIR


def test_a_config_without_the_key_loads_as_it_always_did() -> None:
    config = Config.from_dict(
        {
            "chips": {
                "sources": [
                    {"name": "local", "kind": "dir", "path": "~/chips-private"},
                    {"name": "community", "path": "~/src/chip-data", "enabled": False},
                ]
            }
        }
    )
    assert config.chips.sources == [
        ChipSourceConfig(name="local", kind="dir", path="~/chips-private", enabled=True),
        ChipSourceConfig(name="community", kind="dir", path="~/src/chip-data", enabled=False),
    ]
    assert [source.on_air for source in config.chips.sources] == [True, True]


@pytest.mark.parametrize(
    ("written", "on_air"), [(False, False), (True, True), (0, False), (1, True)]
)
def test_the_key_is_a_switch(written, on_air) -> None:
    config = Config.from_dict(
        {"chips": {"sources": [{"name": "local", "path": "~/p", "on_air": written}]}}
    )
    assert config.chips.sources[0].on_air is on_air


@pytest.mark.parametrize("written", ["false", "no", "true", "", 2, -1, 0.0, [], {}, [False]])
def test_a_key_that_is_not_a_plain_switch_keeps_the_source_off_air(written, caplog) -> None:
    """Somebody wrote the key to hold a source back. A typo mustn't be what
    puts it on the stream — so it fails off air, and says so in the log."""
    with caplog.at_level(logging.WARNING, logger=config_module.__name__):
        config = Config.from_dict(
            {"chips": {"sources": [{"name": "local", "path": "~/p", "on_air": written}]}}
        )
    assert config.chips.sources[0].on_air is False
    assert config.chips.sources[0].enabled is True  # still a source for the operator
    assert any("on_air" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("key", ["on-air", "onair", "On_Air", "ON_AIR", "on air", "off_air"])
@pytest.mark.parametrize("value", [False, True, "no"])
def test_the_switch_misspelt_keeps_the_source_off_air(key, value, caplog) -> None:
    """TOML takes ``on-air = false`` without complaint and it would be ignored —
    an overlay on stream with nothing said. Whatever it's set to, it fails off air."""
    with caplog.at_level(logging.WARNING, logger=config_module.__name__):
        config = Config.from_dict(
            {"chips": {"sources": [{"name": "local", "path": "~/p", key: value}]}}
        )
    assert config.chips.sources[0].on_air is False
    assert any(key in record.getMessage() for record in caplog.records)


def test_a_misspelt_switch_beside_the_real_one_still_fails_off_air() -> None:
    entry = {"name": "local", "path": "~/p", "on_air": True, "on-air": False}
    assert Config.from_dict({"chips": {"sources": [entry]}}).chips.sources[0].on_air is False


@pytest.mark.parametrize("key", ["repair_note", "pair", "airflow", "base_url", "fair"])
def test_a_key_that_only_has_air_in_it_is_not_the_switch(key, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=config_module.__name__):
        config = Config.from_dict(
            {"chips": {"sources": [{"name": "local", "path": "~/p", key: "x"}]}}
        )
    assert config.chips.sources[0].on_air is True
    assert caplog.records == []


def test_a_misspelt_switch_is_saved_as_the_real_one(tmp_path) -> None:
    """The stray key is gone after a save — so what it was for has to be kept."""
    path = tmp_path / "config.toml"
    path.write_text(
        '[[chips.sources]]\nname = "local"\npath = "~/chips-private"\non-air = false\n',
        encoding="utf-8",
    )
    config = config_module.load(path)
    assert config.chips.sources[0].on_air is False
    config_module.save(config, path)
    assert "on_air = false" in path.read_text(encoding="utf-8")
    assert config_module.load(path).chips.sources[0].on_air is False


@pytest.mark.parametrize(
    ("key", "value"), [("on_air", False), ("on_air", "no"), ("on-air", False), ("off_air", True)]
)
def test_the_switch_written_on_the_chips_table_keeps_every_source_off_air(
    key, value, caplog
) -> None:
    """It isn't a key there. But it was written to hold something back."""
    with caplog.at_level(logging.WARNING, logger=config_module.__name__):
        config = Config.from_dict(
            {
                "chips": {
                    key: value,
                    "sources": [
                        {"name": "local", "path": "~/p"},
                        {"name": "community", "path": "~/c", "on_air": True},
                    ],
                }
            }
        )
    assert [source.on_air for source in config.chips.sources] == [False, False]
    assert any("[chips]" in record.getMessage() for record in caplog.records)


def test_the_switch_on_the_chips_table_saying_on_air_changes_nothing(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=config_module.__name__):
        config = Config.from_dict(
            {
                "chips": {
                    "on_air": True,
                    "sources": [
                        {"name": "local", "path": "~/p", "on_air": False},
                        {"name": "community", "path": "~/c"},
                    ],
                }
            }
        )
    # Each source is as it says for itself; the stray key puts nothing on air.
    assert [source.on_air for source in config.chips.sources] == [False, True]
    assert any("[chips]" in record.getMessage() for record in caplog.records)


def test_the_switch_survives_a_save_and_a_load(tmp_path) -> None:
    path = tmp_path / "config.toml"
    config = Config()
    config.chips.sources = [
        ChipSourceConfig("local", path="~/chips-private", on_air=False),
        ChipSourceConfig("community", path="~/src/chip-data"),
    ]
    config_module.save(config, path)
    written = path.read_text(encoding="utf-8")
    assert "on_air = false" in written and "on_air = true" in written
    assert config_module.load(path).chips.sources == config.chips.sources


def test_a_file_written_before_the_key_existed_loads_and_saves(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[chips]\n\n[[chips.sources]]\nname = "community"\nkind = "dir"\n'
        'path = "~/src/chip-data"\nenabled = true\n',
        encoding="utf-8",
    )
    config = config_module.load(path)
    assert config.chips.sources == [ChipSourceConfig("community", path="~/src/chip-data")]
    config_module.save(config, path)
    assert config_module.load(path).chips.sources == config.chips.sources


def test_the_example_config_shows_the_key() -> None:
    example = (REPO / "config.example.toml").read_text(encoding="utf-8")
    assert "on_air = false" in example


# --- building the two views --------------------------------------------------


def test_on_air_is_a_second_composite_over_the_same_sources(tmp_path) -> None:
    both = build_chip_sources(_sources(tmp_path, overlay_on_air=False))
    assert isinstance(both, ChipSources)
    assert isinstance(both.everything, CompositeChipSource)
    assert isinstance(both.on_air, CompositeChipSource)
    assert [source.name for source in both.everything.sources] == ["local", "community"]
    assert [source.name for source in both.on_air.sources] == ["community"]
    # The same object, so a folder is read once and the two can't drift apart.
    assert both.on_air.sources[0] is both.everything.sources[1]


def test_with_every_source_on_air_the_two_views_hold_the_same_sources(tmp_path) -> None:
    both = build_chip_sources(_sources(tmp_path, overlay_on_air=True))
    assert both.on_air.sources == both.everything.sources
    assert both.on_air is not both.everything


def test_the_operator_s_source_is_every_source(tmp_path) -> None:
    entries = _sources(tmp_path, overlay_on_air=False)
    everything = build_chip_source(entries)
    assert [source.name for source in everything.sources] == ["local", "community"]
    merged = everything.merged("EX1234QFN")
    assert merged.chip.package == "QFN-24 (4x4)"  # the overlay still wins there
    assert everything.get("PRIV77SOT") is not None


def test_the_on_air_view_answers_from_the_on_air_sources_only(tmp_path) -> None:
    both = build_chip_sources(_sources(tmp_path, overlay_on_air=False))
    merged = both.on_air.merged("EX1234QFN")
    assert merged.chip.package == _PUBLIC_PACKAGE
    assert merged.chip.description == _PUBLIC_DESCRIPTION
    assert merged.conflicts == ()  # the overlay's disagreement isn't one it knows about
    assert "ZZ7" not in [marking.code for marking in merged.chip.markings]
    assert both.on_air.get("PRIV77SOT") is None
    assert both.on_air.by_marking("P7") == []
    assert both.on_air.by_marking("ZZ7") == []


def test_no_source_on_air_is_a_view_of_nothing_not_the_null_source(tmp_path) -> None:
    """The null source means "no chip data configured", and the card would say
    so. With sources that are all off air that's wrong — and the card's other
    option, saying sources are held back, is a hint. So: data with no chips."""
    both = build_chip_sources(_sources(tmp_path, overlay_on_air=False, community_on_air=False))
    assert isinstance(both.on_air, CompositeChipSource)
    assert both.on_air.sources == ()
    assert both.on_air.get("EX1234QFN") is None
    assert both.on_air.merged("EX1234QFN") is None
    assert both.on_air.by_marking("X1=") == []
    assert both.everything.get("EX1234QFN") is not None


def test_no_sources_at_all_is_the_null_source_both_ways() -> None:
    both = build_chip_sources([])
    assert isinstance(both.everything, NullChipSource)
    assert isinstance(both.on_air, NullChipSource)
    off = [ChipSourceConfig("local", path="/p", enabled=False, on_air=True)]
    assert isinstance(build_chip_sources(off).on_air, NullChipSource)


def test_a_switched_off_source_is_in_neither_view(tmp_path) -> None:
    entries = _sources(tmp_path, overlay_on_air=True)
    entries[0].enabled = False
    both = build_chip_sources(entries)
    assert [source.name for source in both.everything.sources] == ["community"]
    assert [source.name for source in both.on_air.sources] == ["community"]


def test_an_off_air_source_still_takes_its_name(tmp_path) -> None:
    """The same entries make the same sources whatever is on air: an entry
    that clashes with an off-air one above it is left out, not promoted."""
    entries = [
        ChipSourceConfig("chips", path=str(_overlay(tmp_path)), on_air=False),
        ChipSourceConfig("Chips", path=str(_build_dir(tmp_path)), on_air=True),
    ]
    both = build_chip_sources(entries)
    assert [source.name for source in both.everything.sources] == ["chips"]
    assert both.on_air.sources == ()


@pytest.mark.parametrize("value", [None, 1, "true", "yes", object()])
def test_only_a_plain_true_is_on_air(tmp_path, value) -> None:
    class Entry:
        name, kind, enabled = "local", "dir", True
        path = str(_overlay(tmp_path))
        on_air = value

    both = build_chip_sources([Entry()])
    assert len(both.everything.sources) == 1
    assert both.on_air.sources == ()


# --- the card, through the real window ---------------------------------------


def test_the_panel_is_handed_the_on_air_source_and_nothing_else(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    panel = window.chip_panel
    assert panel._chip_source() is window.on_air_chip_source
    assert window.on_air_chip_source is not window.chip_source
    assert [source.name for source in window.on_air_chip_source.sources] == ["community"]
    assert [source.name for source in window.chip_source.sources] == ["local", "community"]
    # The source is all it's given: no window, no dialog, no config to read round it.
    held = [value for value in vars(panel).values() if value is window or value is window._config]
    assert held == []


def test_a_chip_only_an_off_air_source_has_reads_as_not_found_with_no_hint(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    panel = window.chip_panel
    assert window.chip_source.get("PRIV77SOT") is not None  # the operator's screens have it

    panel.set_chip("ZZ-NOT-A-CHIP")
    assert panel.state is State.NOT_FOUND
    for_a_miss = _everything_drawn(panel)

    panel.set_chip("PRIV77SOT")
    assert panel.state is State.NOT_FOUND
    assert panel.chip is None and panel.card.isHidden()
    # Word for word what any miss draws: nothing tells the two apart.
    assert _everything_drawn(panel) == for_a_miss
    assert panel.message_label.text() == chip_panel_module._NOT_FOUND
    _nothing_private(panel)
    for hint in _HINTS:
        assert hint not in panel.message_label.text().lower()


def test_typing_it_into_the_panel_s_own_box_is_no_way_round(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    panel = window.chip_panel
    panel.part_edit.setText("PRIV77SOT")
    panel.part_edit.returnPressed.emit()
    assert panel.state is State.NOT_FOUND
    assert panel.card.isHidden()
    for text in ("Hush Devices", "blue scrap", "SC-70", "P7"):
        assert text not in _everything_drawn(panel)


def test_an_off_air_overlay_first_in_the_order_does_not_win_on_the_card(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    panel = window.chip_panel
    panel.set_chip("EX1234QFN")
    assert panel.state is State.SHOWN
    assert panel.chip.package == _PUBLIC_PACKAGE
    assert panel.description_label.text() == _PUBLIC_DESCRIPTION
    assert _PUBLIC_PACKAGE in _everything_drawn(panel)
    assert "X1=" in _everything_drawn(panel)
    _nothing_private(panel)
    # The overlay disagrees about the package and the description. Off air,
    # that isn't a dispute the card knows about — and saying so would be a hint.
    assert panel.disputed_label.isHidden() and panel.disputed_label.text() == ""

    # Find chip — an operator screen — still merges every source.
    merged = window.chip_source.merged("EX1234QFN")
    assert merged.chip.package == "QFN-24 (4x4)"
    assert merged.chip.description == "From the grey donor board, runs hot"
    assert {conflict.field for conflict in merged.conflicts} == {"package", "description"}


def test_on_air_the_same_overlay_does_win(qtbot, tmp_path, worker_jobs) -> None:
    """The control for the test above: it's the switch that makes the difference."""
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = window.chip_panel
    panel.set_chip("EX1234QFN")
    assert panel.state is State.SHOWN
    assert panel.description_label.text() == "From the grey donor board, runs hot"
    assert "QFN-24" in _everything_drawn(panel)
    assert not panel.disputed_label.isHidden()
    panel.set_chip("PRIV77SOT")
    assert panel.state is State.SHOWN


def test_the_card_takes_the_on_air_source_s_spelling_of_the_part_number(
    qtbot, tmp_path, worker_jobs
) -> None:
    """Show on stream card hands over the merged chip's part number, and the
    first source's spelling is the merged one. The card looks it up and draws
    its own source's spelling, never what it was handed."""
    config = Config()
    config.chips.sources = _sources(tmp_path, overlay_on_air=False)
    folder = tmp_path / "chips-private" / "chips" / "example-semi"
    (folder / "EX1234QFN.toml").unlink()
    (folder / "ex1234qfn.toml").write_text(
        'schema = 1\nmpn = "ex1234qfn"\nmanufacturer = "Example Semi"\n', encoding="utf-8"
    )
    window = MainWindow(config, scanner=_FakeScanner(), on_persist=lambda c: None)
    qtbot.addWidget(window)
    _settle(worker_jobs)
    window.chip_panel._executor = _inline
    assert window.chip_source.get("EX1234QFN").mpn == "ex1234qfn"
    window.show_chip_on_card("ex1234qfn")
    panel = window.chip_panel
    assert panel.state is State.SHOWN
    assert panel.title_label.text() == "EX1234QFN"
    assert "ex1234qfn" not in _everything_drawn(panel)


def test_with_every_source_off_air_the_card_finds_nothing_and_says_nothing_about_it(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, community_on_air=False)
    panel = window.chip_panel
    # Not "no chip data configured": there is chip data, and it'd send the
    # operator looking for a missing source.
    assert panel.state is State.NO_CHIP
    idle = _everything_drawn(panel)
    for mpn in ("EX1234QFN", "PRIV77SOT", "EX9000SOT"):
        panel.set_chip(mpn)
        assert panel.state is State.NOT_FOUND
        assert panel.card.isHidden()
        _nothing_private(panel)
        assert _PUBLIC_DESCRIPTION not in _everything_drawn(panel)
    panel.clear()
    assert _everything_drawn(panel) == idle
    for hint in _HINTS:
        assert hint not in panel.message_label.text().lower()
    # The operator's screens are untouched.
    assert window.chip_source.get("EX1234QFN") is not None
    assert window.chip_source.by_marking("X1=")


def test_show_on_stream_card_for_a_chip_the_card_will_not_show(
    qtbot, tmp_path, worker_jobs
) -> None:
    """What it reads: Find chip (the operator's) says the part number was sent;
    the card (the stream's) says no such chip, like any miss. Neither says why."""
    window = _window(qtbot, tmp_path, worker_jobs)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    dialog.query_edit.setText("PRIV77SOT")
    dialog.lookup_now()
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert dialog.selected().chip.mpn == "PRIV77SOT"
    assert dialog.card_button.isEnabled()

    dialog.card_button.click()

    assert dialog.card_label.text() == sent_line("PRIV77SOT")
    assert dialog.card_label.text() == (
        "Sent PRIV77SOT to the Chip panel — that card is stream output."
    )
    panel = window.chip_panel
    assert panel.state is State.NOT_FOUND
    assert panel.message_label.text() == chip_panel_module._NOT_FOUND
    assert panel.part_edit.text() == ""  # what was handed over isn't put in the box
    _nothing_private(panel)


def test_the_tooltip_says_the_card_shows_what_the_on_air_sources_say() -> None:
    assert "on air" in SHOW_ON_CARD_TIP and "stream output" in SHOW_ON_CARD_TIP


# --- the inventory row: a dock in the same window, so the same rule (#508) -----

_SHELF = [
    Part(id=1, description="Mystery regulator", mpn="PRIV77SOT", status="low"),
    Part(id=2, description="Buck controller", mpn="EX1234QFN", status="low"),
    Part(id=3, description="Unknown IC", mpn="ZZ9999", status="low"),
]
#: The overlay's words. The part numbers aren't in it: a shelf part's MPN is
#: the shop's own data and the row draws it whatever the chip reference says.
_PRIVATE_WORDS = tuple(text for text in _PRIVATE if text.upper() != "PRIV77SOT")


def _row(window: MainWindow, mpn: str):
    """The window's inventory panel with the part for ``mpn`` selected.

    The first call stocks the shelf and has the row's lookups run on the spot;
    later calls only move the selection, so a test can swap the runner.
    """
    panel = window.inventory_panel
    if not isinstance(window._inventory_source, InMemoryInventorySource):
        shelf = InMemoryInventorySource(list(_SHELF))
        window._inventory_source = shelf
        # A Preferences OK rebuilds the inventory source from the config;
        # here that gives the same shelf back.
        window._make_inventory_source = lambda: shelf
        panel._run_in_background = _inline
        panel.refresh()
    panel.part_list.setCurrentRow(-1)
    for row in range(panel.part_list.count()):
        if mpn in panel.part_list.item(row).text():
            panel.part_list.setCurrentRow(row)
            return panel
    raise AssertionError(f"no row for {mpn}")


def _no_private_words(panel: QWidget) -> None:
    drawn = _everything_drawn(panel)
    for text in _PRIVATE_WORDS:
        assert text not in drawn, text


def test_the_inventory_row_is_handed_the_on_air_source_too(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    assert window.inventory_panel._chips() is window.on_air_chip_source
    assert window.inventory_panel._chips() is not window.chip_source
    assert window.inventory_panel.sensitive is False  # and so its tag is true again


def test_a_part_only_an_off_air_source_describes_reads_as_unknown_on_the_row(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    assert window.chip_source.get("PRIV77SOT") is not None  # Find chip has it

    panel = _row(window, "ZZ9999")
    for_an_unknown_part = panel.chip_label.text()
    assert for_an_unknown_part == "ZZ9999 isn't in the chip reference."

    panel = _row(window, "PRIV77SOT")
    # The same line any unknown part gets, with its own number in it.
    assert panel.chip_label.text() == for_an_unknown_part.replace("ZZ9999", "PRIV77SOT")
    assert not panel.chip_block.isHidden()
    assert panel.datasheet_row.isHidden()  # no datasheet line, no buttons
    assert not panel.card_button.isVisibleTo(panel)
    assert panel.show_selected_on_card() is False
    _no_private_words(panel)
    for hint in _HINTS:
        assert hint not in panel.chip_label.text().lower()


def test_a_part_both_describe_shows_the_on_air_words_on_the_row(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    panel = _row(window, "EX1234QFN")
    drawn = _everything_drawn(panel)
    assert _PUBLIC_PACKAGE in drawn and _PUBLIC_DESCRIPTION in drawn
    assert "X1=" in drawn
    _no_private_words(panel)


def test_on_air_the_same_overlay_does_show_on_the_row(qtbot, tmp_path, worker_jobs) -> None:
    """The control: it's the switch that makes the difference."""
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = _row(window, "EX1234QFN")
    assert "From the grey donor board, runs hot" in _everything_drawn(panel)
    panel = _row(window, "PRIV77SOT")
    assert "Pulled from the blue scrap board" in _everything_drawn(panel)
    assert panel.card_button.isVisibleTo(panel)


def test_a_document_only_an_off_air_source_lists_is_not_named_on_the_row(
    qtbot, tmp_path, worker_jobs
) -> None:
    config = Config()
    config.chips.sources = _sources(tmp_path, overlay_on_air=False)
    config.chips.datasheet_dir = str(tmp_path / "datasheets")
    (tmp_path / "datasheets").mkdir()
    (tmp_path / "datasheets" / "donor-teardown-notes.pdf").write_bytes(b"%PDF-1.4 made up")
    chip_file = tmp_path / "chips-private" / "chips" / "example-semi" / "EX1234QFN.toml"
    chip_file.write_text(
        chip_file.read_text(encoding="utf-8")
        + '[[documents]]\nkind = "datasheet"\nfilename = "donor-teardown-notes.pdf"\n',
        encoding="utf-8",
    )
    window = MainWindow(config, scanner=_FakeScanner(), on_persist=lambda c: None)
    qtbot.addWidget(window)
    window.show()
    _settle(worker_jobs)
    merged = window.chip_source.get("EX1234QFN")
    assert "donor-teardown-notes.pdf" in [document.filename for document in merged.documents]

    panel = _row(window, "EX1234QFN")
    assert not panel.datasheet_row.isHidden()
    assert "donor-teardown" not in _everything_drawn(panel)
    # And Open datasheet can't open it: the row was never told it exists.
    held = panel._datasheet
    assert held is None or held.file is None or "donor" not in held.file.path.name


def test_with_every_source_off_air_the_row_knows_no_part(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, community_on_air=False)
    for mpn in ("EX1234QFN", "PRIV77SOT", "ZZ9999"):
        panel = _row(window, mpn)
        assert panel.chip_label.text() == f"{mpn} isn't in the chip reference."
        assert not panel.card_button.isVisibleTo(panel)
        _no_private_words(panel)
        assert _PUBLIC_DESCRIPTION not in _everything_drawn(panel)


def test_show_on_stream_card_is_only_offered_for_a_chip_the_card_will_show(
    qtbot, tmp_path, worker_jobs
) -> None:
    """The row and the card ask the same source, so the row can't say "sent"
    while the card says "no such chip" — which, side by side, was a hint."""
    window = _window(qtbot, tmp_path, worker_jobs)
    panel = _row(window, "EX1234QFN")
    assert panel.card_button.isVisibleTo(panel)
    panel.card_button.click()
    card = window.chip_panel
    assert card.state is State.SHOWN and card.chip.mpn == "EX1234QFN"
    assert panel.message_label.text() == sent_line("EX1234QFN")
    _no_private_words(panel)
    _nothing_private(card)


def test_unticking_on_air_takes_the_overlay_s_words_off_a_selected_row(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = _row(window, "EX1234QFN")
    assert "From the grey donor board, runs hot" in _everything_drawn(panel)

    _untick_on_air(qtbot, window, 0, Qt.CheckState.Unchecked)
    _settle(worker_jobs)

    # OK reloads the shelf, which drops the selection — and nothing of the
    # overlay's is left behind, not even as the text of a hidden label.
    assert panel.chip_block.isHidden()
    _no_private_words(panel)
    panel = _row(window, "EX1234QFN")
    assert _PUBLIC_DESCRIPTION in _everything_drawn(panel)
    _no_private_words(panel)

    _untick_on_air(qtbot, window, 0, Qt.CheckState.Checked)
    _settle(worker_jobs)
    panel = _row(window, "EX1234QFN")
    assert "From the grey donor board, runs hot" in _everything_drawn(panel)


def test_a_selected_row_is_looked_up_again_when_the_switch_changes(
    qtbot, tmp_path, worker_jobs
) -> None:
    """Without a reselect: the window tells the row its source changed."""
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = _row(window, "EX1234QFN")
    assert "From the grey donor board, runs hot" in _everything_drawn(panel)

    window._config.chips.sources[0].on_air = False
    window.reconfigure_chip_source()
    _settle(worker_jobs)

    assert panel.selected_part().mpn == "EX1234QFN"  # still the selected row
    assert _PUBLIC_DESCRIPTION in _everything_drawn(panel)
    _no_private_words(panel)


def test_unticking_on_air_empties_a_row_only_that_source_describes(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = _row(window, "PRIV77SOT")
    assert "Hush Devices" in _everything_drawn(panel)
    assert panel.card_button.isVisibleTo(panel)

    _untick_on_air(qtbot, window, 0, Qt.CheckState.Unchecked)
    _settle(worker_jobs)

    assert panel.show_selected_on_card() is False
    _no_private_words(panel)
    panel = _row(window, "PRIV77SOT")
    assert panel.chip_label.text() == "PRIV77SOT isn't in the chip reference."
    assert not panel.card_button.isVisibleTo(panel)
    assert panel.show_selected_on_card() is False
    _no_private_words(panel)


def test_a_row_answer_from_before_the_switch_never_lands(qtbot, tmp_path, worker_jobs) -> None:
    """A row lookup that started while the overlay was on air, answering after
    it was taken off: its answer has the overlay's words in it and is dropped."""
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = _row(window, "ZZ9999")
    held: list = []
    panel._run_in_background = held.append
    _row(window, "EX1234QFN")
    assert len(held) == 1  # asked of the source that has the overlay in it

    window._config.chips.sources[0].on_air = False
    window.reconfigure_chip_source()
    _settle(worker_jobs)

    held.pop(0)()  # the old answer, overlay and all
    _no_private_words(panel)
    assert panel.show_selected_on_card() is False  # nothing to send until the new answer
    while held:
        held.pop(0)()
    assert _PUBLIC_DESCRIPTION in _everything_drawn(panel)
    _no_private_words(panel)


def test_the_row_is_looked_up_again_even_if_the_card_s_refresh_falls_over(
    qtbot, tmp_path, worker_jobs, monkeypatch
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = _row(window, "EX1234QFN")
    assert "runs hot" in _everything_drawn(panel)

    def broken() -> None:
        raise RuntimeError("the card fell over")

    monkeypatch.setattr(window.chip_panel, "refresh", broken)
    window._config.chips.sources[0].on_air = False
    with pytest.raises(RuntimeError):
        window.reconfigure_chip_source()
    _settle(worker_jobs)
    _no_private_words(panel)


def test_find_chip_is_where_every_source_is_still_looked_up(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    assert dialog._source is window.chip_source
    dialog.query_edit.setText("PRIV77SOT")
    dialog.lookup_now()
    assert dialog.tree.topLevelItemCount() == 1
    assert "Pulled from the blue scrap board" in _everything_drawn(dialog)


# --- flipping the switch -------------------------------------------------------


def _untick_on_air(qtbot, window: MainWindow, row: int, state: Qt.CheckState) -> None:
    dialog = window.open_preferences()
    qtbot.waitUntil(
        lambda: all(
            "Checking…" not in dialog.chips_table.item(r, LOADED).text()
            for r in range(dialog.chips_table.rowCount())
        ),
        timeout=10_000,
    )
    dialog.chips_table.item(row, ON_AIR).setCheckState(state)
    dialog.accept()


def test_unticking_on_air_in_preferences_takes_a_showing_chip_s_overlay_words_down(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = window.chip_panel
    everything = window.chip_source
    panel.set_chip("EX1234QFN")
    assert panel.description_label.text() == "From the grey donor board, runs hot"

    _untick_on_air(qtbot, window, 0, Qt.CheckState.Unchecked)

    assert window._config.chips.sources[0].on_air is False
    assert window.chip_source is not everything  # a new list: rebuilt, no restart
    assert [source.name for source in window.on_air_chip_source.sources] == ["community"]
    assert panel.state is State.SHOWN  # the same chip, as the on-air sources have it
    assert panel.description_label.text() == _PUBLIC_DESCRIPTION
    _nothing_private(panel)

    # And back: ticking it puts the overlay's words on the card again.
    _untick_on_air(qtbot, window, 0, Qt.CheckState.Checked)
    assert window._config.chips.sources[0].on_air is True
    assert panel.description_label.text() == "From the grey donor board, runs hot"


def test_unticking_on_air_takes_down_a_chip_only_that_source_has(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = window.chip_panel
    panel.set_chip("PRIV77SOT")
    assert panel.state is State.SHOWN and "Hush Devices" in _everything_drawn(panel)

    _untick_on_air(qtbot, window, 0, Qt.CheckState.Unchecked)

    assert panel.state is State.NOT_FOUND
    assert panel.card.isHidden() and panel.chip is None
    _nothing_private(panel)


def test_taking_the_last_source_off_air_empties_the_card(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    panel = window.chip_panel
    panel.set_chip("EX1234QFN")
    assert panel.state is State.SHOWN

    _untick_on_air(qtbot, window, 1, Qt.CheckState.Unchecked)

    assert window.on_air_chip_source.sources == ()
    assert panel.state is State.NOT_FOUND
    assert _PUBLIC_DESCRIPTION not in _everything_drawn(panel)


def test_an_answer_from_before_the_switch_never_lands(qtbot, tmp_path, worker_jobs) -> None:
    """A lookup that started while the overlay was on air, answering after it
    was taken off: the answer has the overlay's words in it and is dropped."""
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = window.chip_panel
    held: list = []
    panel._executor = held.append
    panel.set_chip("EX1234QFN")
    assert len(held) == 1  # asked of the source that has the overlay in it

    window._config.chips.sources[0].on_air = False
    window.reconfigure_chip_source()
    _settle(worker_jobs)
    assert len(held) == 2 and panel.state is State.LOOKING

    held[0]()  # the old answer, overlay and all
    assert panel.state is State.LOOKING and panel.card.isHidden()
    _nothing_private(panel)

    held[1]()
    assert panel.state is State.SHOWN
    assert panel.description_label.text() == _PUBLIC_DESCRIPTION
    _nothing_private(panel)


def test_find_chip_keeps_every_source_when_the_switch_is_flipped(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    dialog = window.open_chip_search()
    dialog._executor = _inline

    window._config.chips.sources[0].on_air = False
    window.reconfigure_chip_source()
    _settle(worker_jobs)

    assert dialog._source is window.chip_source
    assert dialog._source is not window.on_air_chip_source
    dialog.query_edit.setText("PRIV77SOT")
    dialog.lookup_now()
    assert dialog.tree.topLevelItemCount() == 1  # an operator screen: it still has it
    assert window.on_air_chip_source.get("PRIV77SOT") is None


def test_the_card_is_refreshed_before_anything_else_can_go_wrong(
    qtbot, tmp_path, worker_jobs, monkeypatch
) -> None:
    """A source just taken off air has to come off the card even if the rest
    of what follows a Preferences OK falls over."""
    window = _window(qtbot, tmp_path, worker_jobs, overlay_on_air=True)
    panel = window.chip_panel
    panel.set_chip("EX1234QFN")
    assert panel.description_label.text() == "From the grey donor board, runs hot"
    dialog = window.open_chip_search()

    def broken(*args, **kwargs):
        raise RuntimeError("Find chip fell over")

    monkeypatch.setattr(dialog, "set_source", broken)
    window._config.chips.sources[0].on_air = False
    with pytest.raises(RuntimeError):
        window.reconfigure_chip_source()

    assert panel.description_label.text() == _PUBLIC_DESCRIPTION
    _nothing_private(panel)


def test_ok_with_nothing_changed_keeps_both_views(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    everything, on_air = window.chip_source, window.on_air_chip_source
    window.reconfigure_chip_source()
    _settle(worker_jobs)
    assert window.chip_source is everything
    assert window.on_air_chip_source is on_air


# --- Preferences → Chips -------------------------------------------------------

_INDEX = LoadReport(chips=412, read_from=FROM_INDEX, origin=Path("/data/out/index.json"))
_FILES = LoadReport(chips=3, read_from=FROM_FILES, origin=Path("/private/chips"))


def _prefs(qtbot, *sources: ChipSourceConfig, **kwargs) -> tuple[PreferencesDialog, Config]:
    config = Config()
    config.chips.sources = list(sources)
    reports = {"/private": _FILES, "/data": _INDEX}
    dialog = PreferencesDialog(
        config,
        _FakeScanner(),
        chip_probe=lambda folder: reports.get(folder, LoadReport(problems=("not there",))),
        run_in_background=_inline,
        **kwargs,
    )
    qtbot.addWidget(dialog)
    return dialog, config


def _on_air_ticks(dialog: PreferencesDialog) -> list[bool]:
    table = dialog.chips_table
    return [
        table.item(row, ON_AIR).checkState() == Qt.CheckState.Checked
        for row in range(table.rowCount())
    ]


def test_the_table_has_an_on_air_column_that_says_what_it_is_for(qtbot) -> None:
    dialog, _config = _prefs(
        qtbot,
        ChipSourceConfig("local", path="/private", on_air=False),
        ChipSourceConfig("community", path="/data"),
    )
    header = dialog.chips_table.horizontalHeaderItem(ON_AIR)
    assert header.text() == "On air"
    assert "stream output" in header.toolTip() and "Find chip" in header.toolTip()
    assert _on_air_ticks(dialog) == [False, True]
    assert "On air" in dialog.chips_order_note.text()
    # A tick box and nothing else: no text to edit, and it can't be typed into.
    cell = dialog.chips_table.item(0, ON_AIR)
    assert cell.text() == ""
    assert cell.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert not cell.flags() & Qt.ItemFlag.ItemIsEditable


def test_unticking_on_air_is_saved_and_nothing_else_about_the_row_changes(qtbot) -> None:
    saved: list[Config] = []
    dialog, config = _prefs(
        qtbot,
        ChipSourceConfig("local", path="/private"),
        ChipSourceConfig("community", path="/data"),
        on_save=saved.append,
    )
    dialog.chips_table.item(0, ON_AIR).setCheckState(Qt.CheckState.Unchecked)
    dialog.accept()
    assert saved == [config]
    assert config.chips.sources == [
        ChipSourceConfig("local", path="/private", enabled=True, on_air=False),
        ChipSourceConfig("community", path="/data", enabled=True, on_air=True),
    ]


def test_ok_without_touching_the_column_saves_what_was_loaded(qtbot) -> None:
    before = [
        ChipSourceConfig("local", path="/private", on_air=False),
        ChipSourceConfig("community", path="/data", enabled=False),
        ChipSourceConfig("parked", path="/parked", enabled=False, on_air=False),
        ChipSourceConfig("example-api", kind="http", on_air=False),
    ]
    dialog, config = _prefs(qtbot, *before)
    dialog.accept()
    assert config.chips.sources == before


@pytest.mark.parametrize("value", ["false", "true", 1, None])
def test_the_box_is_ticked_for_a_plain_true_and_nothing_else(qtbot, value) -> None:
    """The builder only takes a plain True as on air. The box mustn't claim
    more — or OK would turn something that wasn't on air into something that is."""
    entry = ChipSourceConfig("local", path="/private")
    entry.on_air = value
    dialog, config = _prefs(qtbot, entry)
    assert _on_air_ticks(dialog) == [False]
    dialog.accept()
    assert config.chips.sources[0].on_air is False


def test_a_row_with_no_on_air_box_is_off_air(qtbot) -> None:
    dialog, config = _prefs(qtbot, ChipSourceConfig("local", path="/private"))
    dialog.chips_table.takeItem(0, ON_AIR)
    dialog.accept()
    assert config.chips.sources == [ChipSourceConfig("local", path="/private", on_air=False)]


def test_the_switch_travels_with_its_row(qtbot, monkeypatch) -> None:
    dialog, config = _prefs(
        qtbot,
        ChipSourceConfig("local", path="/private", on_air=False),
        ChipSourceConfig("community", path="/data"),
    )
    dialog.chips_table.setCurrentCell(0, NAME)
    dialog.chip_source_down_button.click()
    assert _on_air_ticks(dialog) == [True, False]
    dialog.chip_source_up_button.click()
    assert _on_air_ticks(dialog) == [False, True]

    # Browsing for the row's folder keeps the switch as it was.
    monkeypatch.setattr(
        "benchhud.shell.preferences.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: "/elsewhere",
    )
    dialog.browse_chip_folder_button.click()
    assert _on_air_ticks(dialog) == [False, True]

    # Removing the row above doesn't hand its switch to the one below.
    dialog.chips_table.setCurrentCell(0, NAME)
    dialog.remove_chip_source_button.click()
    assert _on_air_ticks(dialog) == [True]
    dialog.accept()
    assert config.chips.sources == [ChipSourceConfig("community", path="/data")]


def test_a_new_row_takes_the_default(qtbot, monkeypatch) -> None:
    dialog, _config = _prefs(qtbot)
    dialog.add_chip_source_button.click()
    assert _on_air_ticks(dialog) == [CHIP_SOURCE_ON_AIR]
    monkeypatch.setattr(
        "benchhud.shell.preferences.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: "/data",
    )
    dialog.chips_table.setCurrentCell(-1, -1)
    dialog.browse_chip_folder_button.click()
    assert _on_air_ticks(dialog)[-1] is CHIP_SOURCE_ON_AIR


def test_preferences_says_when_no_source_is_on_air(qtbot) -> None:
    """The card gives no hint, so the operator's screen has to: with every
    source off air the Chip panel finds nothing, and this is where it says why."""
    dialog, _config = _prefs(
        qtbot,
        ChipSourceConfig("local", path="/private", on_air=False),
        ChipSourceConfig("community", path="/data"),
    )
    note = dialog.chips_on_air_note
    assert note.text() == NO_SOURCE_ON_AIR and "Chip panel" in note.text()
    assert note.isHidden()

    dialog.chips_table.item(1, ON_AIR).setCheckState(Qt.CheckState.Unchecked)
    assert not note.isHidden()
    dialog.chips_table.item(0, ON_AIR).setCheckState(Qt.CheckState.Checked)
    assert note.isHidden()

    # A source that's switched off isn't on air, whatever its box says.
    dialog.chips_table.item(0, ON).setCheckState(Qt.CheckState.Unchecked)
    assert not note.isHidden()
    # And with no source in use at all there's nothing to say.
    dialog.chips_table.item(1, ON).setCheckState(Qt.CheckState.Unchecked)
    assert note.isHidden()


def test_no_sources_configured_shows_no_note(qtbot) -> None:
    dialog, _config = _prefs(qtbot)
    assert dialog.chips_on_air_note.isHidden()


# --- the guide ---------------------------------------------------------------


def test_the_guide_says_how_to_keep_a_source_off_the_stream() -> None:
    doc = (REPO / "docs" / "CHIPS.md").read_text(encoding="utf-8")
    assert "on_air = false" in doc
    assert "On air" in doc  # the Preferences column
    # The caveat #504 left — "an overlay's description goes on air, and the
    # switch is an open issue" — is answered, so it mustn't still be there.
    assert "issues/482" not in doc
    lowered = " ".join(doc.lower().split())
    # The switch covers the whole HUD window — the card and the inventory row —
    # and Find chip, its own window, is what still uses every source (#508).
    assert "the card and the inventory row look the chip up in the on-air sources" in lowered
    assert "find chip still uses every source" in lowered
    assert "find chip and the inventory row still use" not in lowered
    assert "issues/508" not in doc  # answered, so the "open issue" note is gone
    assert "on-air" in doc and "under `[chips]`" in doc
    assert "before this switch" in lowered  # an older benchhud ignores and drops the key
