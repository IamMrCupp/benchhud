"""Find chip… — from the mark on a chip, or its part number, to what it is.

Used with a board under the microscope, so it's built for the keyboard: type
what's printed on the part, arrow to a candidate, Enter opens its datasheet.

**One box, tried both ways.** What's typed goes to the source as a mark and as
a part number (:func:`benchhud.chips.find.find_chips`). The answers are one
list: the chip whose part number it is comes first, then every chip the text
could be as a mark, in the source's ranked order. A part number is found only
when it's typed in full; a mark is found from its first character.

**Never one silent answer.** A mark is many-to-many — the same three characters
turn up on different chips in different packages — so every candidate is
listed with its package and functions to tell them apart. Several candidates:
none is picked for you. Exactly one: it's shown straight away, there being
nothing to choose between.

**Nothing slow on the GUI thread.** A source can take a moment on its first
read, and finding a document can hash a folder. Both run on a worker and
answer by signal, the way the Flash panel runs flashrom. Lookups wait for a
short pause in typing and run one at a time; if the text moved on while one
ran, its answer is dropped and the newest text is looked up next.

**Opening a document is the caller's business.** The dialog takes an optional
callable — "open this chip's datasheet" or "open this document" — and knows
nothing about where documents are kept. Without one, the action is there but
off, and says why.

Read-only, and nothing raises into the HUD: a source or an opener that fails
gets a log line and a plain message in the status line.

This is an **operator dialog, not stream output**. Nothing in it goes to a
panel or the virtual camera, and it's given chip reference data only — no job,
customer or engagement field comes near it.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.chips.find import Candidate, find_chips
from benchhud.chips.marking import MatchKind, normalise_mark
from benchhud.chips.model import Chip, Document, Substitute
from benchhud.chips.source import ChipSource, NullChipSource
from benchhud.shell.qt_util import hint, notice, set_tone

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenOutcome:
    """What an opener did, for the status line.

    ``detail`` is shown as it is when given — the place to say which file was
    opened and how sure the match is, or why there was nothing to open.
    """

    opened: bool
    detail: str = ""


#: ``(chip, document) -> what happened``. ``document`` is ``None`` for "this
#: chip's datasheet". May be slow (it can hash a folder): it's never called on
#: the GUI thread. A plain ``bool`` is taken as opened / not found.
OpenDocument = Callable[[Chip, Document | None], OpenOutcome | bool]

#: Runs a job off the GUI thread.
Executor = Callable[[Callable[[], None]], None]

#: How long typing has to pause before a lookup starts.
DEBOUNCE_MS = 120
#: A lookup slower than this says it's still looking.
SLOW_MS = 300
#: Rows put in the list. One character can match a great many codes, and the
#: list is ranked, so past this the answer is "keep typing".
MAX_SHOWN = 200

NO_DATA = "No chip data is configured, so there's nothing to look up."
NO_OPENER = "No datasheet folder is set up, so there's nothing to open."
PROMPT = "Type the mark printed on the chip, or a part number in full."
NOT_PICKED = "Nothing picked — ↓ moves through the candidates, Enter opens the picked one."

_COLUMNS = ("Part number", "Maker", "Package", "Functions", "Mark", "Match")
_MARK_COLUMN = 4

_KIND_LABELS = {
    MatchKind.EXACT: "exact",
    MatchKind.WHOLE_MARK: "whole mark",
    MatchKind.PARTIAL: "still typing",
}
_KIND_MEANINGS = {
    MatchKind.EXACT: "the code is exactly what was typed",
    MatchKind.WHOLE_MARK: "what was typed starts with this code",
    MatchKind.PARTIAL: "this code starts with what was typed",
}

#: Keys that move through the candidates while the text box keeps the focus.
_MOVES = {
    Qt.Key.Key_Down: 1,
    Qt.Key.Key_Up: -1,
    Qt.Key.Key_PageDown: 10,
    Qt.Key.Key_PageUp: -10,
}
_ENTER = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


def _thread_executor(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="chip-finder", daemon=True).start()


def match_label(candidate: Candidate) -> str:
    """How a candidate was found, in a few words: the list's last column."""
    parts = []
    if candidate.by_part_number:
        parts.append("part number")
    if candidate.match is not None:
        kind = candidate.match.kind
        parts.append(_KIND_LABELS.get(kind, str(kind.value)))
    return " + ".join(parts)


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count:,} {one if count == 1 else (many or one + 's')}"


class FindChipDialog(QDialog):
    """Type a mark or a part number; get ranked candidates and their details."""

    _looked_up = Signal(str, object)  # (the text looked up, candidates or None)
    _opened = Signal(object, object, object)  # (chip, document or None, outcome)

    def __init__(
        self,
        source: ChipSource,
        open_document: OpenDocument | None = None,
        *,
        open_unavailable: str = NO_OPENER,
        executor: Executor = _thread_executor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find chip")
        self.resize(860, 720)
        self._source = source
        self._configured = not isinstance(source, NullChipSource)
        self._open_document = open_document
        self._open_unavailable = open_unavailable
        self._executor = executor

        self._wanted = ""  # what's typed now
        self._shown = ""  # what the list is the answer to
        self._looking = False
        self._opening = False

        # Fixed pitch wherever a mark is shown: 0/O and 1/l/I matter on one.
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if self.font().pointSizeF() > 0:
            mono.setPointSizeF(self.font().pointSizeF())
        self._mono = mono

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Mark on the chip, or a part number in full")
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.setFont(mono)
        self.query_edit.textChanged.connect(self._on_text_changed)

        self.notice_label = notice(QLabel(NO_DATA), "warning")
        self.notice_label.setVisible(not self._configured)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(_COLUMNS))
        self.tree.setHeaderLabels(list(_COLUMNS))
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.currentItemChanged.connect(lambda *_: self._on_selection())
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected())

        self.title_label = QLabel()
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_font = QFont(self.title_label.font())
        title_font.setBold(True)
        if title_font.pointSizeF() > 0:
            title_font.setPointSizeF(title_font.pointSizeF() * 1.2)
        self.title_label.setFont(title_font)

        self.facts_label = QLabel()
        self.description_label = QLabel()
        self.found_label = hint(QLabel())
        for label in (self.facts_label, self.description_label, self.found_label):
            # Plain text, always: these show what a data file says, and a label
            # left to guess would render markup found in one.
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.details = QTreeWidget()
        self.details.setColumnCount(3)
        self.details.setHeaderHidden(True)
        self.details.setRootIsDecorated(False)
        self.details.setItemsExpandable(False)
        self.details.setAllColumnsShowFocus(True)
        self.details.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.details.itemDoubleClicked.connect(lambda item, _column: self._activate_detail(item))

        detail_pane = QWidget()
        detail_layout = QVBoxLayout(detail_pane)
        detail_layout.setContentsMargins(0, 6, 0, 0)
        detail_layout.addWidget(self.title_label)
        detail_layout.addWidget(self.facts_label)
        detail_layout.addWidget(self.description_label)
        detail_layout.addWidget(self.found_label)
        detail_layout.addWidget(self.details, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tree)
        splitter.addWidget(detail_pane)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        # The detail pane grows when a chip is picked; the list keeps room for
        # half a dozen candidates whatever it does.
        self.tree.setMinimumHeight(190)

        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)

        # With nothing to open documents with, the button is off — and the
        # reason is on the dialog, not only in a tooltip.
        self.open_hint_label = hint(QLabel(open_unavailable))
        self.open_hint_label.setTextFormat(Qt.TextFormat.PlainText)
        self.open_hint_label.setWordWrap(True)
        self.open_hint_label.setVisible(open_document is None)

        self.open_button = QPushButton("Open datasheet")
        self.open_button.clicked.connect(self.open_selected)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(self.open_button, QDialogButtonBox.ButtonRole.ActionRole)
        # Enter belongs to the picked candidate, from the box and from either
        # list (see eventFilter). No button may claim it as the dialog's default.
        for button in buttons.buttons():
            if isinstance(button, QPushButton):
                button.setAutoDefault(False)
                button.setDefault(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self.query_edit)
        layout.addWidget(self.notice_label)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.open_hint_label)
        layout.addWidget(buttons)

        self.setTabOrder(self.query_edit, self.tree)
        self.setTabOrder(self.tree, self.details)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._dispatch)
        self._slow = QTimer(self)
        self._slow.setSingleShot(True)
        self._slow.setInterval(SLOW_MS)
        self._slow.timeout.connect(self._on_slow)

        self._looked_up.connect(self._on_looked_up)
        self._opened.connect(self._on_opened)
        for watched in (self.query_edit, self.tree, self.details):
            watched.installEventFilter(self)

        self._say("" if not self._configured else PROMPT)
        self._on_selection()
        self.query_edit.setFocus()

    # --- keyboard ------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Enter and the arrows, from the text box and from either list.

        Handled here and not left to each widget, because what a list does
        with Enter differs by platform (macOS tries to edit the row) and a
        dialog would hand it to a default button.
        """
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in _ENTER:
                if watched is self.details:
                    self._activate_detail(self.details.currentItem())
                else:
                    self._enter()
                return True
            if watched is self.query_edit and key in _MOVES:
                self._move(_MOVES[key])
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event: QShowEvent) -> None:
        """Opened again: the box has the focus, ready to be typed over."""
        super().showEvent(event)
        self.query_edit.setFocus()
        self.query_edit.selectAll()

    def _enter(self) -> None:
        """Enter is the next step: look up, then pick, then open."""
        if not self._settled():
            self.lookup_now()  # never act on a list that answers older text
        elif self.tree.currentItem() is None:
            self._move(1)
        else:
            self.open_selected()

    def _move(self, by: int) -> None:
        count = self.tree.topLevelItemCount()
        if not count:
            return
        current = self.tree.currentItem()
        if current is None:
            index = 0 if by > 0 else count - 1
        else:
            index = max(0, min(count - 1, self.tree.indexOfTopLevelItem(current) + by))
        self.tree.setCurrentItem(self.tree.topLevelItem(index))

    # --- lookup ---------------------------------------------------------------

    def _on_text_changed(self) -> None:
        self._wanted = normalise_mark(self.query_edit.text())
        if not self._configured:
            return
        if not self._wanted:
            self._debounce.stop()
            self._show("", [])
            return
        self._debounce.start()

    def lookup_now(self) -> None:
        """Look up what's typed now, without waiting for a pause in typing."""
        self._debounce.stop()
        self._dispatch()

    def _settled(self) -> bool:
        """Whether the list is the answer to what's typed now."""
        return not self._looking and self._wanted == self._shown

    def _dispatch(self) -> None:
        if self._looking or not self._configured or self._wanted == self._shown:
            return  # one at a time: _on_looked_up starts the next
        text = self._wanted
        self._looking = True
        self._slow.start()
        if not self._run(lambda: self._look_up(text)):
            self._looking = False
            self._slow.stop()
            self._show(text, None)

    def _run(self, job: Callable[[], None]) -> bool:
        try:
            self._executor(job)
        except Exception:
            log.exception("chip finder: could not start a worker")
            return False
        return True

    def _look_up(self, text: str) -> None:
        """The lookup itself. Runs off the GUI thread."""
        found: list[Candidate] | None
        try:
            found = find_chips(self._source, text)
        except Exception:
            log.exception("chip finder: looking up %r failed", text)
            found = None
        with contextlib.suppress(RuntimeError):  # the dialog went while it ran
            self._looked_up.emit(text, found)

    def _on_slow(self) -> None:
        if self._looking:
            self._say(f"Looking up “{self._wanted}”…")

    def _on_looked_up(self, text: str, found: list[Candidate] | None) -> None:
        self._looking = False
        self._slow.stop()
        if text == self._wanted:
            self._show(text, found)
        elif not self._debounce.isActive():
            self._dispatch()  # typed on while it ran: the newest text is next

    def _show(self, text: str, found: list[Candidate] | None) -> None:
        """Put a lookup's answer in the list. ``None`` is a lookup that failed."""
        self._shown = text
        candidates = found or []
        kept = self.selected()
        keep_key = kept.chip.key if kept is not None else None

        self.tree.blockSignals(True)
        self.tree.clear()
        pick: QTreeWidgetItem | None = None
        for candidate in candidates[:MAX_SHOWN]:
            chip = candidate.chip
            mark = candidate.match.marking.code if candidate.match is not None else "—"
            item = QTreeWidgetItem(
                [
                    chip.mpn,
                    chip.manufacturer,
                    chip.package or "—",
                    ", ".join(chip.functions) or "—",
                    mark,
                    match_label(candidate),
                ]
            )
            item.setFont(_MARK_COLUMN, self._mono)
            item.setData(0, Qt.ItemDataRole.UserRole, candidate)
            self.tree.addTopLevelItem(item)
            if chip.key == keep_key:
                pick = item
        for column in range(len(_COLUMNS)):
            self.tree.resizeColumnToContents(column)
        if pick is None and len(candidates) == 1:
            pick = self.tree.topLevelItem(0)  # nothing to choose between
        self.tree.setCurrentItem(pick)
        self.tree.blockSignals(False)
        self._on_selection()

        if found is None:
            self._say("The lookup failed — the log has the reason.", "error")
        elif not text:
            self._say(PROMPT)
        elif not candidates:
            self._say(f"No chip matches “{text}”. A part number has to be typed in full.")
        elif len(candidates) > MAX_SHOWN:
            self._say(
                f"Showing the best {MAX_SHOWN:,} of {len(candidates):,} candidates — keep typing."
            )
        elif len(candidates) == 1:
            self._say("1 candidate.")
        else:
            self._say(f"{len(candidates):,} candidates, best match first.")

    # --- the picked candidate -------------------------------------------------

    def selected(self) -> Candidate | None:
        """The candidate picked in the list, if one is."""
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)

    def _on_selection(self) -> None:
        candidate = self.selected()
        self.details.clear()
        if candidate is None:
            waiting = self.tree.topLevelItemCount() > 0
            self._set_line(self.title_label, "")
            self._set_line(self.facts_label, "")
            self._set_line(self.description_label, "")
            self._set_line(self.found_label, NOT_PICKED if waiting else "")
        else:
            self._show_chip(candidate)
        self._refresh_open_button()

    def _show_chip(self, candidate: Candidate) -> None:
        chip = candidate.chip
        self._set_line(self.title_label, f"{chip.mpn} — {chip.manufacturer}")
        facts = [chip.package or "", ", ".join(chip.functions)]
        if chip.family:
            facts.append(f"family {chip.family}")
        self._set_line(self.facts_label, "  ·  ".join(f for f in facts if f))
        self._set_line(self.description_label, chip.description or "")
        self._set_line(self.found_label, self._found_text(candidate))

        can_open = self._open_document is not None
        self._section(
            "Markings",
            [((m.code, ", ".join(m.examples), m.note or ""), None, "") for m in chip.markings],
            mono_first=True,
        )
        self._section(
            "Documents",
            [
                (
                    (d.kind, d.filename, _pages(d)),
                    d,
                    "Enter opens this document." if can_open else self._open_unavailable,
                )
                for d in chip.documents
            ],
        )
        self._section(
            "Substitutes",
            [
                ((s.mpn, s.note or "", ""), s, "Enter looks this part number up.")
                for s in chip.substitutes
            ],
        )
        self._section(
            "Seen on",
            [((u.board, u.designator or "", u.role or ""), None, "") for u in chip.used_in],
        )

    @staticmethod
    def _set_line(label: QLabel, text: str) -> None:
        label.setText(text)
        label.setVisible(bool(text))

    @staticmethod
    def _found_text(candidate: Candidate) -> str:
        parts = []
        if candidate.by_part_number:
            parts.append("Found by its part number.")
        match = candidate.match
        if match is not None:
            meaning = _KIND_MEANINGS.get(match.kind, str(match.kind.value))
            case = "" if match.exact_case else ", in a different case"
            parts.append(f"Found by the mark {match.marking.code} — {meaning}{case}.")
        if candidate.chip.sources:
            parts.append(f"Credited to {', '.join(candidate.chip.sources)}.")
        return "  ".join(parts)

    def _section(
        self,
        title: str,
        rows: list[tuple[tuple[str, str, str], object, str]],
        *,
        mono_first: bool = False,
    ) -> None:
        """One titled group in the detail list; an empty one says so."""
        head = QTreeWidgetItem([title if rows else f"{title} — none listed"])
        head.setFlags(Qt.ItemFlag.ItemIsEnabled)
        bold = QFont(self.details.font())
        bold.setBold(True)
        head.setFont(0, bold)
        self.details.addTopLevelItem(head)
        head.setFirstColumnSpanned(True)
        for texts, data, tip in rows:
            row = QTreeWidgetItem(list(texts))
            row.setData(0, Qt.ItemDataRole.UserRole, data)
            if mono_first:
                row.setFont(0, self._mono)
            if tip:
                for column in range(3):
                    row.setToolTip(column, tip)
            head.addChild(row)
        head.setExpanded(True)

    def _activate_detail(self, item: QTreeWidgetItem | None) -> None:
        """Enter or a double-click on a detail row: open it, or look it up."""
        data = None if item is None else item.data(0, Qt.ItemDataRole.UserRole)
        candidate = self.selected()
        if isinstance(data, Document) and candidate is not None:
            self._open(candidate.chip, data)
        elif isinstance(data, Substitute):
            self.query_edit.setText(data.mpn)
            self.query_edit.setFocus()
            self.lookup_now()

    # --- opening a document ---------------------------------------------------

    def _cant_open(self, chip: Chip | None) -> str | None:
        """Why there's nothing to open right now, or ``None`` when there is."""
        if self._open_document is None:
            return self._open_unavailable
        if chip is None:
            return "Pick a candidate first."
        if not chip.documents:
            return f"No documents are listed for {chip.mpn}."
        if self._opening:
            return "Still looking for the last one."
        return None

    def _refresh_open_button(self) -> None:
        candidate = self.selected()
        reason = self._cant_open(None if candidate is None else candidate.chip)
        self.open_button.setEnabled(reason is None)
        self.open_button.setToolTip(reason or "Open the picked chip's datasheet (Enter).")

    def open_selected(self) -> bool:
        """Open the picked chip's datasheet.

        ``False`` when nothing was started, with the reason in the status line.
        ``True`` means the opener is running; what it did arrives by signal.
        """
        candidate = self.selected()
        if candidate is None:
            return False
        return self._open(candidate.chip, None)

    def _open(self, chip: Chip, document: Document | None) -> bool:
        reason = self._cant_open(chip)
        opener = self._open_document
        if reason is not None or opener is None:
            self._say(reason or self._open_unavailable, "warning")
            return False
        self._opening = True
        self._refresh_open_button()
        self._say(f"Looking for {_what(chip, document)}…")
        if not self._run(lambda: self._do_open(opener, chip, document)):
            self._on_opened(chip, document, RuntimeError("no worker"))
            return False
        return True

    def _do_open(self, opener: OpenDocument, chip: Chip, document: Document | None) -> None:
        """Find and open the file. Runs off the GUI thread."""
        outcome: object
        try:
            outcome = opener(chip, document)
        except Exception as exc:
            log.exception("chip finder: opening %s failed", _what(chip, document))
            outcome = exc
        with contextlib.suppress(RuntimeError):  # the dialog went while it ran
            self._opened.emit(chip, document, outcome)

    def _on_opened(self, chip: Chip, document: Document | None, outcome: object) -> None:
        self._opening = False
        self._refresh_open_button()
        what = _what(chip, document)
        if isinstance(outcome, Exception):
            self._say(f"Couldn't open {what} — the log has the reason.", "error")
            return
        result = outcome if isinstance(outcome, OpenOutcome) else OpenOutcome(bool(outcome))
        if result.opened:
            self._say(result.detail or f"Opened {what}.", "ok")
        else:
            self._say(result.detail or f"No file found for {what}.", "warning")

    def _say(self, text: str, tone: str | None = None) -> None:
        self.status_label.setText(text)
        set_tone(self.status_label, tone)


def _pages(document: Document) -> str:
    pages = _plural(document.pages, "page") if document.pages else ""
    return " — ".join(part for part in (pages, document.note or "") if part)


def _what(chip: Chip, document: Document | None) -> str:
    return document.filename if document is not None else f"the datasheet for {chip.mpn}"
