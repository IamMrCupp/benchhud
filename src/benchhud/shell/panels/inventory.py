"""Inventory panel — shop stock at the bench, consumption onto the job.

Backed by the :class:`~benchhud.inventory.source.InventorySource` seam: the
low-stock list by default, a search box for everything else, and a "Use on
job" action that records consumption against the selected job's ticket ref.

Two rules inherited from the seam and enforced here rather than papered over:

* ``status`` is server-derived (``out`` / ``low`` / ``ok``) with the same rule
  the shop tablet's colours use — this panel colours by it and never
  re-derives it, so HUD and shelf always agree.
* A failed consume is **shown, not retried**. The write goes into the shop's
  movements ledger and is not idempotent; the operator decides what happens
  next, not a retry loop.

Everything on :class:`~benchhud.inventory.source.Part` is PII-free, so unlike
ticket customer fields this panel is safe to composite into the stream.

Chip details
------------

When a chip source is configured, selecting a part that has an MPN shows what
the chip reference knows about that part number — maker, package, functions,
description, markings — and offers **Open datasheet** for the file the
datasheet library holds. That's generic reference about a part number: it
names no job, no customer and no engagement, so the panel is exactly as
stream-safe as it was.

- **No chip source configured:** none of this exists. The panel looks and
  behaves as it did before chips.
- **A part with no MPN:** nothing is shown — there's nothing to look up.
- **An MPN the reference doesn't know:** one quiet line saying so, so "no
  answer" can't be mistaken for "still looking".
- **No datasheet held:** the button is off and the line says which file the
  chip lists, so you know what to go and find.
- **A datasheet found by name only** is the weaker claim — maybe another
  revision. The line above the button says so before it's opened, the button
  says "(by name)", and the confirmation after opening says it again.

**Only the chip's own name for a document is ever shown** — the last part of
it, cut short if it's long. Never the name or folder of the file on this
machine: that's somebody's disk, this panel can be on stream, and a file found
by its hash can be called anything. The file is opened, not named.

**Show on stream card** sends the row's chip to the Chip panel, which is
stream output. It's there only when the chip reference knows the part. It
takes a mouse click: the button never holds the keyboard focus, so no key
reaches it, and nothing sends as a side effect of selecting a row. All it
hands over is the chip's part number — the one the lookup for the selected
row came back with, so a row passed over on the way never gets sent.

The lookup can be slow the first time and hashing a folder of PDFs can be slow
any time, so neither runs here: a worker does both and signals back. Every
request carries a number, and an answer to anything but the latest is dropped
— changing the selection mid-lookup never shows the last part's chip. One
worker runs at a time; arrowing down the list queues only the row you stop on.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from benchhud.chips.card import SHOW_ON_CARD, SHOW_ON_CARD_TIP, sent_line
from benchhud.chips.datasheets import ChipDocument, DatasheetLibrary, open_document
from benchhud.chips.model import Chip
from benchhud.chips.opening import (
    find_datasheet,
    held_line,
    not_held_line,
    opened_line,
)
from benchhud.chips.source import ChipSource, NullChipSource
from benchhud.inventory.source import InventorySource, InventoryWriteError, Part
from benchhud.shell.panel import PANEL_MARGINS, Panel

log = logging.getLogger(__name__)

InventorySourceProvider = Callable[[], InventorySource]
#: Where chips are looked up right now (the main window can swap it).
ChipSourceProvider = Callable[[], ChipSource]
#: Where held documents are found right now.
DatasheetLibraryProvider = Callable[[], DatasheetLibrary]
#: Runs a job off the GUI thread.
Executor = Callable[[Callable[[], None]], None]
#: Opens a held document; False when it couldn't.
DocumentOpener = Callable[[Path], bool]
#: Puts the chip with this part number on the stream card. The part number only.
ShowOnCard = Callable[[str], object]
#: The selected job's external ticket ref, or None when no job / not linked.
JobRefProvider = Callable[[], str | None]
#: Called after a successful consume, e.g. to drop a note on the job timeline.
ConsumedHook = Callable[[Part, int], None]

_STATUS_COLOURS = {"out": QColor("#d9534f"), "low": QColor("#e8a33d")}


def _thread_executor(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="chips-lookup", daemon=True).start()


def chip_summary(chip: Chip) -> str:
    """What the chip reference knows about a part number, a line per fact.

    Reference only: the part, who makes it, its package, what it does and
    what's printed on it. Nothing here is about a job or whose board it is —
    the boards a chip has been seen on are left out on purpose.
    """
    lines = [f"{chip.mpn}  ·  {chip.manufacturer}" if chip.manufacturer else chip.mpn]
    what = [text for text in (chip.package, ", ".join(chip.functions)) if text]
    if what:
        lines.append("  ·  ".join(what))
    if chip.description:
        lines.append(chip.description)
    codes = [marking.code for marking in chip.markings if marking.code]
    if codes:
        lines.append("Marked " + ", ".join(codes))
    return "\n".join(lines)


def datasheet_status(
    chip: Chip, held: ChipDocument | None, *, library_configured: bool
) -> tuple[str, str]:
    """``(the line above the button, the button's text)`` for a chip's datasheet.

    A name match says it's a name match in both, so it can be told from an
    exact one before the file is opened. The lines are
    :mod:`benchhud.chips.opening`'s, shared with the Find chip dialog.
    """
    if held is not None and held.file is not None:
        button = "Open datasheet" if held.file.exact else "Open datasheet (by name)"
        return held_line(held), button
    return not_held_line(chip, library_configured=library_configured), "Open datasheet"


def _row_text(part: Part) -> str:
    """One list row: name, count vs min, location — readable at arm's length."""
    name = part.mpn or part.description
    bits = [name, f"{part.quantity_on_hand} on hand (min {part.min_quantity})"]
    if part.location:
        bits.append(part.location)
    return "  ·  ".join(bits)


class InventoryPanel(Panel):
    panel_id = "inventory"
    panel_title = "Inventory"

    #: (request, the chip or None) — the chip reference's answer, from the worker.
    _chip_found = Signal(int, object)
    #: (request, the held file or None) — the datasheet library's, and the worker is done.
    _datasheet_found = Signal(int, object)

    def __init__(
        self,
        source: InventorySourceProvider,
        job_ref: JobRefProvider | None = None,
        on_consumed: ConsumedHook | None = None,
        chips: ChipSourceProvider | None = None,
        datasheets: DatasheetLibraryProvider | None = None,
        run_in_background: Executor = _thread_executor,
        open_datasheet: DocumentOpener = open_document,
        show_on_card: ShowOnCard | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._job_ref = job_ref
        self._on_consumed = on_consumed
        self._chips = chips
        self._datasheets = datasheets
        self._run_in_background = run_in_background
        self._open_datasheet = open_datasheet
        self._show_on_card = show_on_card
        # The latest lookup asked for; answers to earlier ones are dropped.
        self._lookup = 0
        # (request, mpn) waiting for the worker to be free, and whether it's busy.
        self._lookup_waiting: tuple[int, str] | None = None
        self._lookup_running = False
        self._chip: Chip | None = None
        self._datasheet: ChipDocument | None = None
        self._chip_found.connect(self._on_chip_found)
        self._datasheet_found.connect(self._on_datasheet_found)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_search_row())
        self.part_list = QListWidget()
        self.part_list.setMinimumHeight(90)
        self.part_list.currentItemChanged.connect(lambda *_: self._sync_use_row())
        self.part_list.currentItemChanged.connect(lambda *_: self.sync_chip_details())
        layout.addWidget(self.part_list)
        layout.addLayout(self._build_use_row())
        layout.addWidget(self._build_chip_block())
        self.message_label = QLabel()
        self.message_label.setObjectName("InventoryMessage")
        self.message_label.setWordWrap(True)
        # Never rich text: some of what it shows comes from chip data.
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setVisible(False)
        layout.addWidget(self.message_label)

        self.refresh()

    # --- construction ----------------------------------------------------

    def _build_search_row(self) -> QHBoxLayout:
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search parts… (empty = low stock)")
        self.search_edit.returnPressed.connect(self.refresh)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(self.search_edit, stretch=1)
        row.addWidget(self.refresh_button)
        return row

    def _build_use_row(self) -> QHBoxLayout:
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 9999)
        self.use_button = QPushButton("Use on job")
        self.use_button.setToolTip(
            "Record parts consumed — logged against the selected job's ticket when linked"
        )
        self.use_button.clicked.connect(self.consume_selected)
        row = QHBoxLayout()
        row.addWidget(QLabel("Qty"))
        row.addWidget(self.qty_spin)
        row.addWidget(self.use_button)
        row.addStretch(1)
        return row

    def _build_chip_block(self) -> QWidget:
        """The chip details and the datasheet row — hidden until there's a chip to show."""
        # What these show comes from chip data, which anyone can write. Plain
        # text, so a tag in a description is shown and not obeyed; and no say
        # in the panel's width, so a long unbroken word is clipped instead of
        # shoving the dock wide and the composite off the screen.
        no_say_in_width = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        no_say_in_width.setHeightForWidth(True)
        self.chip_label = QLabel()
        self.chip_label.setObjectName("InventoryChip")
        self.chip_label.setWordWrap(True)
        self.chip_label.setTextFormat(Qt.TextFormat.PlainText)
        self.chip_label.setSizePolicy(no_say_in_width)
        self.chip_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.datasheet_button = QPushButton("Open datasheet")
        self.datasheet_button.setToolTip("Open the datasheet held for this chip")
        self.datasheet_button.clicked.connect(self.open_selected_datasheet)
        self.datasheet_label = QLabel()
        self.datasheet_label.setObjectName("InventoryDatasheet")
        self.datasheet_label.setWordWrap(True)
        self.datasheet_label.setTextFormat(Qt.TextFormat.PlainText)
        self.datasheet_label.setSizePolicy(no_say_in_width)
        # The line gets a row to itself, above the button: beside it, a line
        # that wraps to three in a narrow dock had only the button's height.
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addWidget(self.datasheet_button)
        button_row.addStretch(1)
        # The chip is what goes; the datasheet has nothing to do with it. It's
        # in this block because the block exists exactly when the chip is
        # known — and on a row of its own, because two buttons side by side
        # are wider than the dock's default column.
        self.card_button = QPushButton(SHOW_ON_CARD)
        self.card_button.setToolTip(SHOW_ON_CARD_TIP)
        self.card_button.clicked.connect(self.show_selected_on_card)
        # This puts something on air: it takes the mouse. It never holds the
        # focus, so Space can't press it — nor press it again after a click —
        # and it's nobody's default button.
        self.card_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.card_button.setAutoDefault(False)
        self.card_button.setDefault(False)
        if self._show_on_card is None:
            # Hidden, never shown-then-hidden: showing a button that has no
            # parent yet would flash a window of its own on the bench screen.
            self.card_button.setVisible(False)
        card_row = QHBoxLayout()
        card_row.setContentsMargins(0, 0, 0, 0)
        card_row.addWidget(self.card_button)
        card_row.addStretch(1)
        self.datasheet_row = QWidget()
        rows = QVBoxLayout(self.datasheet_row)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.addWidget(self.datasheet_label)
        rows.addLayout(button_row)
        rows.addLayout(card_row)

        self.chip_block = QWidget()
        block = QVBoxLayout(self.chip_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.addWidget(self.chip_label)
        block.addWidget(self.datasheet_row)
        self.chip_block.setVisible(False)
        return self.chip_block

    # --- chip details ----------------------------------------------------

    def _chip_source(self) -> ChipSource | None:
        """The chip source to ask, or ``None`` when chips aren't configured."""
        if self._chips is None:
            return None
        source = self._chips()
        return None if isinstance(source, NullChipSource) else source

    def sync_chip_details(self) -> None:
        """Show the selected part's chip — or nothing, when there's none to show.

        Called when the selection changes and when the list is rebuilt; the
        main window can call it when the chip source or the datasheet folder
        changes under a selection.
        """
        self._lookup += 1  # whatever was being looked up is no longer wanted
        self._lookup_waiting = None
        self._chip = None
        self._datasheet = None
        part = self.selected_part()
        mpn = (part.mpn or "").strip() if part is not None else ""
        if not mpn or self._chip_source() is None:
            self.chip_block.setVisible(False)
            return
        self.chip_label.setText(f"Looking up {mpn}…")
        self.datasheet_row.setVisible(False)
        self.chip_block.setVisible(True)
        self._lookup_waiting = (self._lookup, mpn)
        if not self._lookup_running:
            self._start_lookup()

    def _start_lookup(self) -> None:
        """Hand the waiting lookup to the worker: the chip first, then its datasheet."""
        waiting, source = self._lookup_waiting, self._chip_source()
        self._lookup_waiting = None
        if waiting is None or source is None:
            return
        request, mpn = waiting
        library = self._datasheets() if self._datasheets is not None else None
        chip_found, datasheet_found = self._chip_found, self._datasheet_found

        def job() -> None:
            chip: Chip | None = None
            held: ChipDocument | None = None
            try:
                answer = source.get(mpn)
                chip = answer if isinstance(answer, Chip) else None
            except Exception:  # sources promise not to raise; a lookup must not either
                log.exception("inventory: looking up %r failed", mpn)
            # The panel may have gone while the worker was busy.
            with contextlib.suppress(RuntimeError):
                chip_found.emit(request, chip)
            if chip is not None and library is not None:
                try:
                    held = find_datasheet(library, chip)  # this is the part that hashes
                except Exception:
                    log.exception("inventory: finding the datasheet for %r failed", mpn)
            with contextlib.suppress(RuntimeError):
                datasheet_found.emit(request, held)

        self._lookup_running = True
        try:
            self._run_in_background(job)
        except Exception as exc:
            # No worker to be had: say so, don't sit on "Looking up…".
            log.error("inventory: couldn't start a chip lookup: %s", exc)
            self._lookup_running = False
            if request == self._lookup:
                self.chip_label.setText(f"Couldn't look up {mpn}: {exc}")

    def _on_chip_found(self, request: int, chip: object) -> None:
        if request != self._lookup:
            return  # an answer for a part that's no longer selected
        part = self.selected_part()
        mpn = (part.mpn or "").strip() if part is not None else ""
        if not isinstance(chip, Chip):
            self.chip_label.setText(f"{mpn} isn't in the chip reference.")
            self.datasheet_row.setVisible(False)
            return
        self._chip = chip
        self.chip_label.setText(chip_summary(chip))
        self.datasheet_button.setText("Open datasheet")
        self.datasheet_button.setEnabled(False)
        self.datasheet_label.setText("Checking the datasheet folder…")
        self.datasheet_row.setVisible(True)

    def _on_datasheet_found(self, request: int, held: object) -> None:
        self._lookup_running = False
        if request == self._lookup and self._chip is not None:
            library = self._datasheets() if self._datasheets is not None else None
            self._datasheet = held if isinstance(held, ChipDocument) else None
            line, button = datasheet_status(
                self._chip,
                self._datasheet,
                library_configured=library is not None and library.configured,
            )
            self.datasheet_label.setText(line)
            self.datasheet_button.setText(button)
            self.datasheet_button.setEnabled(self._datasheet is not None)
        if self._lookup_waiting is not None:
            self._start_lookup()  # the selection moved on while the worker was busy

    def show_selected_on_card(self) -> bool:
        """Put the selected part's chip on the stream card. True when it was sent.

        Only once the chip reference has answered for the row that's selected
        now — ``_chip`` is cleared the moment the selection moves — and only
        its part number is handed over.
        """
        chip, send = self._chip, self._show_on_card
        if chip is None or send is None or self._chip_source() is None:
            return False  # nothing known for this row, or chips were switched off since
        try:
            send(chip.mpn)
        except Exception:
            log.exception("inventory: could not put a chip on the stream card")
            self._show_message("Couldn't put it on the Chip panel.", error=True)
            return False
        self._show_message(sent_line(chip.mpn), error=False)
        return True

    def open_selected_datasheet(self) -> None:
        """Open the file held for the selected part's chip, and say what was opened."""
        held = self._datasheet
        if held is None or held.file is None:
            return
        opened = self._open_datasheet(held.file.path)
        self._show_message(opened_line(held, opened), error=not opened)

    # --- behaviour -------------------------------------------------------

    def refresh(self) -> None:
        """Reload the list: search results when a query is set, else low stock."""
        query = self.search_edit.text().strip()
        source = self._source()
        parts = source.search(query) if query else source.list_low_stock()
        # Quietly: clearing a list makes its next row current for a moment, and
        # that row would be looked up — and its datasheet hashed — for nothing.
        self.part_list.blockSignals(True)
        try:
            self.part_list.clear()
            for part in parts:
                item = QListWidgetItem(_row_text(part))
                item.setData(Qt.ItemDataRole.UserRole, part)
                colour = _STATUS_COLOURS.get(part.status)
                if colour is not None:
                    item.setForeground(colour)
                self.part_list.addItem(item)
        finally:
            self.part_list.blockSignals(False)
        if not parts and source.name == "none":
            self._show_message("No inventory backend configured — see Preferences.", error=False)
        elif not parts and not query:
            self._show_message("Nothing below minimum — the shelf is healthy.", error=False)
        else:
            self.message_label.setVisible(False)
        self._sync_use_row()
        self.sync_chip_details()

    def selected_part(self) -> Part | None:
        item = self.part_list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def consume_selected(self) -> None:
        """Record the spinbox quantity against the selected part.

        Failure is shown in the banner and nothing is retried — the movement
        ledger must never be double-counted by an eager retry.
        """
        part = self.selected_part()
        if part is None:
            self._show_message("Select a part first.", error=True)
            return
        quantity = self.qty_spin.value()
        ref = self._job_ref() if self._job_ref is not None else None
        try:
            updated = self._source().consume(part.id, quantity, ticket_ref=ref)
        except InventoryWriteError as exc:
            self._show_message(f"Not recorded: {exc}", error=True)
            return
        name = updated.mpn or updated.description
        suffix = f" on {ref}" if ref else ""
        self._show_message(
            f"Used {quantity} \u00d7 {name}{suffix} — {updated.quantity_on_hand} left.", error=False
        )
        if self._on_consumed is not None:
            self._on_consumed(updated, quantity)
        self.refresh_keeping_message()

    def refresh_keeping_message(self) -> None:
        """Refresh the list without wiping the just-shown confirmation."""
        text, visible = self.message_label.text(), self.message_label.isVisible()
        self.refresh()
        if visible:
            self.message_label.setText(text)
            self.message_label.setVisible(True)

    def _sync_use_row(self) -> None:
        self.use_button.setEnabled(self.selected_part() is not None)

    def _show_message(self, text: str, *, error: bool) -> None:
        self.message_label.setText(text)
        self.message_label.setStyleSheet("color: #d9534f;" if error else "")
        self.message_label.setVisible(True)
