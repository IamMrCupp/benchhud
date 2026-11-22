"""Show on stream card — sending a chip to the Chip panel (#491).

From Find chip and from the inventory row. The card is stream output, so the
things pinned here are: it's an explicit click and never a side effect; the
part number is the only thing that crosses; and a stale chip is never sent.

Offscreen, no network; nothing opens a real file or browser. Every chip and
part is made up — "Example Semi" doesn't exist.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from benchhud.chips import (
    Chip,
    CompositeChipSource,
    Document,
    InMemoryChipSource,
    Marking,
    NullChipSource,
    Substitute,
    UsedIn,
    builder,
)
from benchhud.chips import datasheets as datasheets_module
from benchhud.chips.card import NOTHING_TO_SHOW, SHOW_ON_CARD, SHOW_ON_CARD_TIP, sent_line
from benchhud.config import ChipSourceConfig, Config
from benchhud.inventory import InMemoryInventorySource, Part
from benchhud.shell import chip_dialog
from benchhud.shell.chip_dialog import FindChipDialog
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.chip import State
from benchhud.shell.panels.inventory import InventoryPanel

REPO = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "chip-data" / "index.json"

# Everything a contributor or the operator can write freely is filled in, so a
# test can check none of it rides along with the part number.
_BUCK = Chip(
    mpn="EX1234QFN",
    manufacturer="Example Semi",
    package="QFN-20 (3x3)",
    functions=("buck-controller",),
    description="Single-phase buck controller for a standby rail",
    sources=("credit-to-someone",),
    markings=(Marking("X1=", ("X1=6A",), "note on the mark"),),
    documents=(Document("datasheet", "EX1234.pdf", note="note on the document"),),
    substitutes=(Substitute("EX1235QFN", "note on the substitute"),),
    used_in=(UsedIn("EXAMPLE-BOARD-1", "U7000", "standby 3.3 V"),),
)
_BUCK_LOCAL = Chip(
    mpn="ex1234qfn",  # the operator's own spelling
    manufacturer="Example Semi",
    package="QFN-24 (4x4)",
    description="From the grey donor board, runs hot",
    markings=(Marking("X1=", note="private note"),),
)
_LDO = Chip(
    mpn="EX9000SOT",
    manufacturer="Example Semi",
    package="SOT-23-5",
    functions=("ldo",),
    markings=(Marking("X1=", ("X1=2B",)),),
)
_NOT_THE_PART_NUMBER = (
    "Example Semi",
    "QFN-2",  # the package, "QFN-20" or "QFN-24" — "QFN" alone is in the part number
    "buck",
    "Single-phase",
    "credit-to-someone",
    "note",
    "EX1235QFN",
    "EXAMPLE-BOARD-1",
    "U7000",
    "grey donor",
    "local",
    "community",
    "X1=",
)

_PARTS = [
    Part(
        id=1,
        description="Buck controller for Jordan Example's laptop",
        mpn="  ex1234qfn ",
        status="low",
        quantity_on_hand=2,
        location="Shelf A3",
        project="TICKET-1042",
    ),
    Part(id=2, description="LDO", mpn="EX9000SOT", status="low"),
    Part(id=3, description="Kapton tape", status="low"),
    Part(id=4, description="Mystery IC", mpn="ZZ9999", status="low"),
]


class _FakeScanner:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


class _Sent(list):
    """Records exactly what a "show on card" call was handed."""

    def __call__(self, *args, **kwargs) -> None:
        self.append((args, kwargs))


@pytest.fixture(autouse=True)
def nothing_leaves(monkeypatch):
    """No real browser, no real file opened."""
    reached: list[object] = []
    monkeypatch.setattr(chip_dialog, "open_in_browser", lambda url: reached.append(url) or False)
    monkeypatch.setattr(datasheets_module, "default_os_open", reached.append)
    yield
    assert reached == []


def _inline(job) -> None:
    job()


def _two_sources() -> CompositeChipSource:
    return CompositeChipSource(
        [
            InMemoryChipSource([_BUCK_LOCAL], "local"),
            InMemoryChipSource([_BUCK, _LDO], "community"),
        ]
    )


def _dialog(qtbot, source=None, **kwargs) -> FindChipDialog:
    kwargs.setdefault("executor", _inline)
    source = InMemoryChipSource([_BUCK, _LDO], "community") if source is None else source
    dialog = FindChipDialog(source, **kwargs)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def _type(dialog: FindChipDialog, text: str) -> None:
    dialog.query_edit.setText(text)
    dialog.lookup_now()


def _key(widget, key) -> None:
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(widget, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))


def _panel(qtbot, *, chips=None, sent=None, run=_inline, parts=_PARTS) -> InventoryPanel:
    inventory = InMemoryInventorySource(list(parts))
    if chips is None:
        chips = _two_sources()
    panel = InventoryPanel(
        lambda: inventory,
        job_ref=lambda: "TICKET-1042 Jordan Example",
        chips=(lambda: chips) if chips is not False else None,
        run_in_background=run,
        show_on_card=sent,
    )
    qtbot.addWidget(panel)
    panel.show()
    return panel


def _select(panel: InventoryPanel, text: str) -> None:
    for row in range(panel.part_list.count()):
        if text in panel.part_list.item(row).text():
            panel.part_list.setCurrentRow(row)
            return
    raise AssertionError(f"no row for {text}")


def _only_the_part_number(sent: _Sent, mpn: str) -> None:
    """One call, one argument, and it is the chip's part number and nothing else."""
    assert sent == [((mpn,), {})]
    (((handed,), _kwargs),) = sent
    assert type(handed) is str
    for text in _NOT_THE_PART_NUMBER:
        assert text not in handed


# --- the words ----------------------------------------------------------------


def test_the_action_says_it_is_the_stream() -> None:
    assert SHOW_ON_CARD == "Show on stream card"
    assert "stream output" in SHOW_ON_CARD_TIP
    assert "replaces" in SHOW_ON_CARD_TIP
    assert (
        sent_line("EX1234QFN") == "EX1234QFN is on the Chip panel now — that card is stream output."
    )


# --- Find chip ----------------------------------------------------------------


def test_with_nowhere_to_send_a_chip_there_is_no_button(qtbot) -> None:
    dialog = _dialog(qtbot)
    _type(dialog, "EX1234QFN")
    assert dialog.selected() is not None
    assert not dialog.card_button.isVisibleTo(dialog)
    assert dialog.show_selected_on_card() is False


@pytest.mark.parametrize(
    ("source", "configured"),
    [(NullChipSource(), None), (CompositeChipSource([]), False)],
)
def test_with_no_chip_data_the_button_is_not_left_dangling(qtbot, source, configured) -> None:
    sent = _Sent()
    dialog = _dialog(qtbot, source, show_on_card=sent, configured=configured)
    assert not dialog.card_button.isVisibleTo(dialog)
    assert dialog.show_selected_on_card() is False
    assert sent == []


def test_the_button_is_off_until_a_chip_is_picked(qtbot) -> None:
    sent = _Sent()
    dialog = _dialog(qtbot, show_on_card=sent)
    assert dialog.card_button.isVisibleTo(dialog)
    assert dialog.card_button.text() == SHOW_ON_CARD
    assert not dialog.card_button.isEnabled()
    assert dialog.card_button.toolTip() == NOTHING_TO_SHOW
    _type(dialog, "X1=")  # two candidates: none is picked for you
    assert not dialog.card_button.isEnabled()
    dialog.card_button.click()
    assert sent == []
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert dialog.card_button.isEnabled()
    assert dialog.card_button.toolTip() == SHOW_ON_CARD_TIP


def test_a_click_sends_the_picked_chip_s_part_number_and_nothing_else(qtbot) -> None:
    sent = _Sent()
    dialog = _dialog(qtbot, _two_sources(), show_on_card=sent)
    _type(dialog, "X1=")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    picked = dialog.selected().chip
    # The pane is showing notes, source names and a value that lost.
    assert dialog.selected().merged.conflicts
    dialog.card_button.click()
    _only_the_part_number(sent, picked.mpn)
    assert picked.mpn == "ex1234qfn"  # the merged chip's part number, as the first source has it


def test_nothing_but_the_click_sends_anything(qtbot) -> None:
    sent = _Sent()
    dialog = _dialog(qtbot, show_on_card=sent, open_document=lambda chip, document: True)
    _type(dialog, "EX1234QFN")  # one candidate: it's picked for you
    assert dialog.selected() is not None
    for row in range(dialog.tree.topLevelItemCount()):
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(row))
    _key(dialog.query_edit, Qt.Key.Key_Return)  # Enter opens the datasheet, as ever
    _key(dialog.tree, Qt.Key.Key_Return)
    _key(dialog.query_edit, Qt.Key.Key_Down)
    dialog.tree.itemDoubleClicked.emit(dialog.tree.topLevelItem(0), 0)
    assert sent == []


def test_no_key_can_reach_the_button(qtbot) -> None:
    dialog = _dialog(qtbot, show_on_card=_Sent())
    assert dialog.card_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not dialog.card_button.isDefault()
    assert not dialog.card_button.autoDefault()


def test_the_dialog_says_what_went_on_air_and_forgets_it_for_the_next_chip(qtbot) -> None:
    dialog = _dialog(qtbot, show_on_card=_Sent())
    assert not dialog.card_label.isVisibleTo(dialog)
    _type(dialog, "X1=")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    mpn = dialog.selected().chip.mpn
    assert dialog.show_selected_on_card() is True
    assert dialog.card_label.isVisibleTo(dialog)
    assert dialog.card_label.text() == sent_line(mpn)
    assert dialog.card_label.textFormat() == Qt.TextFormat.PlainText
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1))  # another chip: that was the last one
    assert not dialog.card_label.isVisibleTo(dialog)
    assert dialog.card_label.text() == ""


def test_no_chip_text_is_in_the_button_s_tooltip(qtbot) -> None:
    dialog = _dialog(qtbot, _two_sources(), show_on_card=_Sent())
    _type(dialog, "X1=")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    for text in ("ex1234qfn", "EX1234QFN", "grey donor", "QFN-24", "local"):
        assert text not in dialog.card_button.toolTip()
        assert text not in dialog.card_label.toolTip()


def test_a_card_that_cannot_take_it_is_said_not_raised(qtbot) -> None:
    def broken(mpn: str) -> None:
        raise RuntimeError("the panel fell over")

    dialog = _dialog(qtbot, show_on_card=broken)
    _type(dialog, "EX1234QFN")
    assert dialog.show_selected_on_card() is False
    assert dialog.card_label.text() == "Couldn't put it on the Chip panel — the log has the reason."


def test_the_button_follows_the_chip_data_coming_and_going(qtbot) -> None:
    dialog = _dialog(qtbot, show_on_card=_Sent())
    _type(dialog, "EX1234QFN")
    assert dialog.card_button.isVisibleTo(dialog) and dialog.card_button.isEnabled()
    dialog.set_source(NullChipSource())
    assert not dialog.card_button.isVisibleTo(dialog)
    dialog.set_source(InMemoryChipSource([_LDO], "new"))
    assert dialog.card_button.isVisibleTo(dialog)
    assert not dialog.card_button.isEnabled()  # the old pick isn't in the new source


# --- the inventory row ----------------------------------------------------------


def test_with_nowhere_to_send_a_chip_the_row_has_no_such_button(qtbot) -> None:
    panel = _panel(qtbot, sent=None)
    _select(panel, "ex1234qfn")
    assert panel.chip_block.isVisible()
    assert not panel.card_button.isVisibleTo(panel)
    assert panel.show_selected_on_card() is False


@pytest.mark.parametrize("chips", [False, NullChipSource()])
def test_with_no_chip_source_there_is_no_button_and_the_panel_is_as_it_was(qtbot, chips) -> None:
    sent = _Sent()
    plain = _panel(qtbot, chips=False, sent=None)
    panel = _panel(qtbot, chips=chips, sent=sent)
    for row in range(panel.part_list.count()):
        panel.part_list.setCurrentRow(row)
        assert not panel.card_button.isVisibleTo(panel)
        assert panel.show_selected_on_card() is False
    assert sent == []
    assert panel.minimumSizeHint() == plain.minimumSizeHint()
    assert panel.sizeHint() == plain.sizeHint()


def test_only_a_part_the_chip_reference_knows_has_the_button(qtbot) -> None:
    sent = _Sent()
    panel = _panel(qtbot, sent=sent)
    for text in ("Kapton tape", "ZZ9999"):  # no MPN; an MPN nobody knows
        _select(panel, text)
        assert not panel.card_button.isVisibleTo(panel)
        assert panel.show_selected_on_card() is False
    _select(panel, "EX9000SOT")
    assert panel.card_button.isVisibleTo(panel)
    assert panel.card_button.isEnabled()
    assert panel.card_button.text() == SHOW_ON_CARD
    assert panel.card_button.toolTip() == SHOW_ON_CARD_TIP
    assert sent == []  # selecting rows sent nothing


def test_the_row_sends_the_chip_s_part_number_and_nothing_else(qtbot) -> None:
    sent = _Sent()
    panel = _panel(qtbot, sent=sent)
    _select(panel, "ex1234qfn")
    panel.card_button.click()
    # The chip reference's part number — not the shelf's spelling of it, and
    # nothing about the part, the shelf, the job or the customer.
    _only_the_part_number(sent, "ex1234qfn")
    (((handed,), _),) = sent
    for text in ("Jordan", "TICKET-1042", "Shelf A3", "laptop", "Buck controller", " "):
        assert text not in handed
    assert panel.message_label.text() == sent_line("ex1234qfn")
    assert "TICKET" not in panel.message_label.text()


def test_a_row_passed_over_mid_lookup_is_never_what_gets_sent(qtbot) -> None:
    sent = _Sent()
    jobs: list = []
    panel = _panel(qtbot, sent=sent, run=jobs.append)
    _select(panel, "ex1234qfn")
    assert panel.show_selected_on_card() is False  # not known yet: nothing to send
    _select(panel, "EX9000SOT")  # the operator moves on before the answer
    jobs.pop(0)()  # the answer for the first row arrives now
    assert panel.show_selected_on_card() is False  # and is not what's selected
    assert sent == []
    jobs.pop(0)()
    assert panel.show_selected_on_card() is True
    _only_the_part_number(sent, "EX9000SOT")


def test_sending_starts_no_lookup_of_its_own(qtbot) -> None:
    asked: list[str] = []

    class Counting(InMemoryChipSource):
        def get(self, mpn: str):
            asked.append(mpn)
            return super().get(mpn)

    panel = _panel(qtbot, chips=Counting([_BUCK], "community"), sent=_Sent())
    _select(panel, "ex1234qfn")
    assert len(asked) == 1
    panel.card_button.click()
    panel.card_button.click()
    assert len(asked) == 1  # the row's own lookup is the only one


def test_a_card_that_cannot_take_it_is_said_on_the_row(qtbot) -> None:
    def broken(mpn: str) -> None:
        raise RuntimeError("the panel fell over")

    panel = _panel(qtbot, sent=broken)
    _select(panel, "EX9000SOT")
    assert panel.show_selected_on_card() is False
    assert panel.message_label.text() == "Couldn't put it on the Chip panel."


# --- the main window: both callers, one card -----------------------------------


@pytest.fixture
def worker_jobs(monkeypatch) -> list:
    """Jobs handed to the chips worker thread, held instead of run."""
    jobs: list = []
    monkeypatch.setattr(builder, "_thread", jobs.append)
    return jobs


def _settle(worker_jobs: list) -> None:
    while worker_jobs:
        worker_jobs.pop()()


def _build_dir(tmp_path: Path, name: str = "build") -> Path:
    build = tmp_path / name
    build.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, build / "index.json")
    return build


def _overlay(tmp_path: Path) -> Path:
    folder = tmp_path / "chips-private" / "chips" / "example-semi"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "EX1234QFN.toml").write_text(
        'schema = 1\nmpn = "EX1234QFN"\nmanufacturer = "Example Semi"\n'
        'description = "From the grey donor board, runs hot"\n'
        '[[markings]]\ncode = "X1="\nnote = "private note"\n'
    )
    return tmp_path / "chips-private"


def _window(qtbot, tmp_path, worker_jobs, *, sources=True) -> MainWindow:
    config = Config()
    if sources:
        config.chips.sources = [
            ChipSourceConfig("local", path=str(_overlay(tmp_path))),
            ChipSourceConfig("community", path=str(_build_dir(tmp_path))),
        ]
    window = MainWindow(config, scanner=_FakeScanner(), on_persist=lambda c: None)
    qtbot.addWidget(window)
    window.show()
    _settle(worker_jobs)
    window.chip_panel._executor = _inline
    return window


def test_find_chip_hands_the_card_a_part_number_and_nothing_else(
    qtbot, tmp_path, worker_jobs, monkeypatch
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    sent = _Sent()
    monkeypatch.setattr(window.chip_panel, "set_chip", sent)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "X1=")
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
    assert dialog.selected().merged.conflicts  # two sources, and they disagree
    dialog.card_button.click()
    _only_the_part_number(sent, "EX1234QFN")


def test_the_inventory_row_hands_the_card_a_part_number_and_nothing_else(
    qtbot, tmp_path, worker_jobs, monkeypatch
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    sent = _Sent()
    monkeypatch.setattr(window.chip_panel, "set_chip", sent)
    panel = window.inventory_panel
    window._inventory_source = InMemoryInventorySource(list(_PARTS))
    panel.refresh()
    panel._run_in_background = _inline
    _select(panel, "ex1234qfn")
    panel.card_button.click()
    _only_the_part_number(sent, "EX1234QFN")


def test_sending_puts_the_chip_on_the_card_and_replaces_the_one_there(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    window.show_chip_on_card("EX9000SOT")
    assert window.chip_panel.state is State.SHOWN
    assert window.chip_panel.chip.mpn == "EX9000SOT"
    dialog = window.open_chip_search()
    dialog._executor = _inline
    _type(dialog, "EX1234QFN")
    dialog.card_button.click()  # no confirmation: it just replaces
    assert window.chip_panel.chip.mpn == "EX1234QFN"
    # The card looked the chip up itself: it holds the merged chip, not a copy
    # of anything the dialog was showing.
    assert window.chip_panel.chip == window.chip_source.get("EX1234QFN")


def test_sending_brings_the_chip_tab_forward_even_if_it_was_closed(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs)
    dock = window.panels.dock("chip")
    dock.hide()  # closed from the View menu
    assert not dock.isVisible()
    window.show_chip_on_card("EX1234QFN")
    assert dock.isVisible()
    assert not dock.visibleRegion().isEmpty()  # in front of the tabs it shares a row with
    assert window.chip_panel.chip.mpn == "EX1234QFN"


def test_a_dialog_opened_before_the_sources_changed_still_sends_to_the_new_ones(
    qtbot, tmp_path, worker_jobs
) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, sources=False)
    dialog = window.open_chip_search()
    dialog._executor = _inline
    assert not dialog.card_button.isVisibleTo(dialog)  # no chip data: no button

    prefs = window.open_preferences()
    prefs.add_chip_source_button.click()
    prefs.chips_table.item(0, 0).setText("community")
    prefs.chips_table.item(0, 1).setText(str(_build_dir(tmp_path, "later")))
    prefs.accept()
    _settle(worker_jobs)

    assert window.open_chip_search() is dialog
    assert dialog.card_button.isVisibleTo(dialog)
    _type(dialog, "SD400BGA")
    dialog.card_button.click()
    assert window.chip_panel.state is State.SHOWN
    assert window.chip_panel.chip.mpn == "SD400BGA"


def test_with_no_chip_sources_neither_action_is_there(qtbot, tmp_path, worker_jobs) -> None:
    window = _window(qtbot, tmp_path, worker_jobs, sources=False)
    dialog = window.open_chip_search()
    assert not dialog.card_button.isVisibleTo(dialog)
    panel = window.inventory_panel
    window._inventory_source = InMemoryInventorySource(list(_PARTS))
    panel.refresh()
    for row in range(panel.part_list.count()):
        panel.part_list.setCurrentRow(row)
        assert panel.chip_block.isHidden()
        assert not panel.card_button.isVisibleTo(panel)


def test_the_guide_says_what_the_action_puts_on_air() -> None:
    doc = (REPO / "docs" / "CHIPS.md").read_text(encoding="utf-8")
    assert doc.count("Show on stream card") >= 3  # Find chip, the row, the panel
    assert "only the part number" in doc.lower()
