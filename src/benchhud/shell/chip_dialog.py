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
nothing to choose between — and it's let go again the moment the list grows.
Only a row the operator picked is kept while they type on.

**The keys never act on an old list.** A lookup waits for a pause in typing,
so the list can be a moment behind the box. Enter and the arrows look the
current text up first; an arrow pressed in that moment is applied to the new
list when it arrives.

**Nothing slow on the GUI thread.** A source can take a moment on its first
read, and finding a document can hash a folder. Both run on a worker and
answer by signal, the way the Flash panel runs flashrom. Lookups run one at a
time; if the text moved on while one ran, its answer is dropped and the newest
text is looked up next.

**Opening a document is the caller's business.** The dialog takes an optional
callable — "open this chip's datasheet" or "open this document" — and knows
nothing about where documents are kept. Without one, the action is there but
off, and the dialog says why.

**What a data file says is shown as text.** Chip data comes from files someone
else may have written. Every label that shows it is plain-text, and none of it
goes into a tooltip, which Qt would render as markup.

**Link-outs are a click, and can be locked.** When your own data doesn't have
the chip, the mark can be looked up on a marking site, and a document can be
opened where its maker publishes it (:mod:`benchhud.chips.linkout`). Either
tells a third party what's on the bench. So an address is only ever handed to
the browser by a click on a button that names the host — never as you type —
and only what's typed, or the document's own address, goes with it. The
caller says whether linking out is allowed right now, the way it supplies the
datasheet opener; the main window answers no while a recon engagement is
active. **With nothing to ask, the answer is no**: a button that's wrongly off
costs a trip to the browser by hand, one that's wrongly on names a client's
part to a stranger. The question is asked again at every click. benchhud
itself still makes no network request.

Read-only, and nothing raises into the HUD: a source or an opener that fails
gets a log line and a plain message.

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

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontDatabase, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.chips.find import Candidate, find_chips
from benchhud.chips.linkout import (
    MARKING_SITES,
    MarkingSite,
    WebAddress,
    document_address,
    sendable,
)
from benchhud.chips.marking import MatchKind, normalise_mark
from benchhud.chips.model import Chip, Document, Substitute
from benchhud.chips.source import ChipSource, NullChipSource
from benchhud.shell.browser import open_in_browser
from benchhud.shell.qt_util import hint, notice, set_tone

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenOutcome:
    """What an opener did, for the dialog to say.

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

#: "May this window link out right now?" Asked on the GUI thread at every
#: click and whenever the buttons are redrawn, so it has to be quick.
LinkOutAllowed = Callable[[], bool]
#: Hands an address to the operator's browser; true when it was taken.
OpenUrl = Callable[[str], object]

#: How long typing has to pause before a lookup starts.
DEBOUNCE_MS = 120
#: A lookup slower than this says it's still looking.
SLOW_MS = 300
#: Rows put in the list. One character can match a great many codes, and the
#: list is ranked, so past this the answer is "keep typing".
MAX_SHOWN = 200
#: Longest text the box takes. A code is 16 characters at most and a part
#: number not much more; this only stops a stray paste from being a lookup.
MAX_TYPED = 100

NO_DATA = "No chip data is configured, so there's nothing to look up."
NO_OPENER = "No datasheet folder is set up, so there's nothing to open."
PROMPT = "Type the mark printed on the chip, or a part number in full."
NOT_PICKED = "Nothing picked — ↓ moves through the candidates."
NOT_PICKED_ENTER = NOT_PICKED[:-1] + ", Enter opens the picked one's datasheet."

LINK_OUT_LOCKED = (
    "Web lookups are off while a recon engagement is active — "
    "they'd tell an outside site what's on the bench."
)
LINK_OUT_UNWIRED = "Web lookups are off: nothing has told this window they're allowed."
WEB_PROMPT = "Type a mark to look it up on the marking sites."
NOT_SENDABLE = "Only a plain mark, of 40 characters at most, is sent to a marking site."
NO_MAKERS_PAGE = "No maker's page is listed for this."
REFUSED_ADDRESS = "The address listed isn't a plain https:// one, so it isn't opened."

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
_COMMAND_KEYS = (
    Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
)


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


def _plural(count: int, one: str) -> str:
    return f"{count:,} {one if count == 1 else one + 's'}"


def _data_label(label: QLabel) -> QLabel:
    """Set ``label`` up to show text that came from a data file or the box.

    Plain text, always: a label left to guess would render markup found in a
    chip file. And it wraps without ever asking for width — one long unbroken
    string in a file mustn't be what sizes the dialog.
    """
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


class FindChipDialog(QDialog):
    """Type a mark or a part number; get ranked candidates and their details.

    ``configured`` says whether there is chip data to look in. Left at
    ``None`` it's taken from the source: a :class:`NullChipSource` is "none
    configured". A caller whose source can be empty for want of config (a
    directory source with a blank path, a merge of no sources) passes
    ``False``, so the dialog says that and not "no chip matches".
    """

    _looked_up = Signal(str, object)  # (the text looked up, candidates or None)
    _opened = Signal(object, object, object)  # (chip, document or None, outcome)

    def __init__(
        self,
        source: ChipSource,
        open_document: OpenDocument | None = None,
        *,
        configured: bool | None = None,
        open_unavailable: str = NO_OPENER,
        link_out_allowed: LinkOutAllowed | None = None,
        link_out_locked: str = LINK_OUT_LOCKED,
        open_url: OpenUrl = open_in_browser,
        executor: Executor = _thread_executor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find chip")
        self.resize(860, 760)
        self._source = source
        if configured is None:
            configured = not isinstance(source, NullChipSource)
        self._configured = configured
        self._open_document = open_document
        self._open_unavailable = open_unavailable
        self._link_out_allowed = link_out_allowed
        self._link_out_locked = link_out_locked
        self._open_url = open_url
        self._executor = executor

        self._wanted = ""  # what's typed now
        # What the list is the answer to. None after a lookup that failed:
        # nothing typed equals it, so Enter looks the same text up again.
        self._shown: str | None = ""
        self._total = 0  # candidates found, shown or not
        self._failed = False
        self._looking = False
        self._auto_picked = False  # the pick is the dialog's, not the operator's
        self._pending_move = 0  # arrows pressed while the list was behind
        self._detail_key: str | None = None  # the chip in the detail pane
        self._opening = False
        self._opening_from = ""  # the box's text when the opener started
        # What became of the last link-out, until the text or the pick moves on.
        self._web_said: tuple[str, str | None] | None = None

        # Fixed pitch wherever a mark is shown: 0/O and 1/l/I matter on one.
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if self.font().pointSizeF() > 0:
            mono.setPointSizeF(self.font().pointSizeF())
        self._mono = mono

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Mark on the chip, or a part number in full")
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.setMaxLength(MAX_TYPED)
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
        self.tree.currentItemChanged.connect(lambda *_: self._on_operator_pick())
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected())

        self.title_label = _data_label(QLabel())
        title_font = QFont(self.title_label.font())
        title_font.setBold(True)
        if title_font.pointSizeF() > 0:
            title_font.setPointSizeF(title_font.pointSizeF() * 1.2)
        self.title_label.setFont(title_font)
        self.facts_label = _data_label(QLabel())
        self.description_label = _data_label(QLabel())
        self.found_label = _data_label(hint(QLabel()))
        for label in (
            self.title_label,
            self.facts_label,
            self.description_label,
            self.found_label,
        ):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.details = QTreeWidget()
        self.details.setColumnCount(3)
        self.details.setHeaderHidden(True)
        self.details.setRootIsDecorated(False)
        self.details.setItemsExpandable(False)
        self.details.setAllColumnsShowFocus(True)
        self.details.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.details.itemDoubleClicked.connect(lambda item, _column: self._activate_detail(item))
        self.details.currentItemChanged.connect(lambda *_: self._refresh_web())

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

        # Two lines, so neither talks over the other: what the lookup found,
        # and what became of opening a document. With nothing to open
        # documents with, the second holds the reason — on the dialog, not
        # only in a tooltip.
        self.status_label = _data_label(QLabel())
        self.open_label = _data_label(QLabel())
        if open_document is None:
            hint(self.open_label).setText(open_unavailable)

        # The link-outs: one button per marking site, named for the host it
        # opens, and a line that says what would be sent — or why they're off.
        self.web_label = _data_label(QLabel())
        self.site_buttons: list[QPushButton] = []
        web_row = QHBoxLayout()
        web_row.addWidget(self.web_label, 1)
        for site in MARKING_SITES:
            site_button = QPushButton(site.host)
            # Never the dialog's default: Enter must not be able to link out.
            site_button.setAutoDefault(False)
            site_button.setDefault(False)
            site_button.clicked.connect(lambda _checked=False, site=site: self.search_on(site))
            self.site_buttons.append(site_button)
            web_row.addWidget(site_button)
        self.maker_button = QPushButton("Maker's page")
        self.maker_button.clicked.connect(self.open_makers_page)

        self.open_button = QPushButton("Open datasheet")
        self.open_button.clicked.connect(self.open_selected)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(self.open_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.maker_button, QDialogButtonBox.ButtonRole.ActionRole)
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
        layout.addWidget(self.open_label)
        layout.addLayout(web_row)
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
        """Enter from the box and both lists; the arrows from the box; and
        typing in a list, which goes to the box.

        Enter is handled here and not left to each widget, because what a
        list does with it differs by platform (macOS tries to edit the row)
        and a dialog would hand it to a default button. A held Enter counts
        once. Typing with a list focused would otherwise be the list's own
        search, which moves the pick without a word.
        """
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in _ENTER:
                if event.isAutoRepeat():
                    pass
                elif watched is self.details:
                    self._activate_detail(self.details.currentItem())
                else:
                    self._enter()
                return True
            if watched is self.query_edit:
                if key in _MOVES:
                    self._move(_MOVES[key])
                    return True
            elif self._types_text(event):
                self.query_edit.setFocus()
                QCoreApplication.sendEvent(
                    self.query_edit, QKeyEvent(event.type(), key, event.modifiers(), event.text())
                )
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _types_text(event: QKeyEvent) -> bool:
        text = event.text()
        return bool(text) and text.isprintable() and not (event.modifiers() & _COMMAND_KEYS)

    def showEvent(self, event: QShowEvent) -> None:
        """Opened again: the box has the focus, ready to be typed over.

        Only when the app shows it. A window restored from the dock or
        another desktop keeps the focus it had.
        """
        super().showEvent(event)
        if not event.spontaneous():
            self.query_edit.setFocus()
            self.query_edit.selectAll()
        self._refresh_web()  # an engagement may have started while this was away

    def changeEvent(self, event: QEvent) -> None:
        """Coming back to the window asks again whether link-outs are allowed:
        the operator may have just started an engagement in the main window."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._refresh_web()

    def _enter(self) -> None:
        """Enter is the next step: look up, then pick, then open."""
        if not self._configured:
            return
        if not self._settled():
            # Never act on a list that answers older text.
            if self._looking:
                self._say(f"Looking up “{self._wanted}”…")
            self.lookup_now()
        elif self.tree.currentItem() is None:
            self._step(1)
        else:
            self.open_selected()

    def _move(self, by: int) -> None:
        """An arrow key. While the list is behind the box, it's kept for the
        list that's coming rather than spent on the one about to go."""
        if self._settled():
            self._step(by)
        elif self._configured:
            self._pending_move += by
            self.lookup_now()

    def _step(self, by: int) -> None:
        count = self.tree.topLevelItemCount()
        if not count:
            return
        current = self.tree.currentItem()
        if current is not None:
            start = self.tree.indexOfTopLevelItem(current)
        else:  # nothing picked: one step down is the top row, one up the bottom
            start = -1 if by > 0 else count
        index = max(0, min(count - 1, start + by))
        self.tree.setCurrentItem(self.tree.topLevelItem(index))

    # --- lookup ---------------------------------------------------------------

    def _on_text_changed(self) -> None:
        self._wanted = normalise_mark(self.query_edit.text())
        self._pending_move = 0
        self._web_said = None
        self._refresh_web()  # builds addresses for the buttons; sends nothing
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
        """The lookup itself. Runs off the GUI thread.

        Whatever the source does, an answer goes back — a lookup that never
        answered would hold every later one up behind it.
        """
        found: list[Candidate] | None = None
        try:
            found = find_chips(self._source, text)
        except BaseException:
            log.exception("chip finder: looking up %r failed", text)
        with contextlib.suppress(RuntimeError):  # the dialog went while it ran
            self._looked_up.emit(text, found)

    def _on_slow(self) -> None:
        if self._looking and self._wanted and self._wanted != self._shown:
            self._say(f"Looking up “{self._wanted}”…")

    def _on_looked_up(self, text: str, found: list[Candidate] | None) -> None:
        self._looking = False
        self._slow.stop()
        if text == self._wanted:
            try:
                self._show(text, found)
            except Exception:  # a record the list can't draw: not the HUD's problem
                log.exception("chip finder: could not show the answer for %r", text)
                self._show(text, None)
            return
        # Typed on while it ran: that answer is dropped and the newest text is next.
        if not self._debounce.isActive():
            self._dispatch()
        if self._settled():
            self._say_result()  # back at text the list already answers

    def _show(self, text: str, found: list[Candidate] | None) -> None:
        """Put a lookup's answer in the list. ``None`` is a lookup that failed."""
        self._shown = None if found is None else text
        self._failed = found is None
        candidates = found or []
        self._total = len(candidates)
        kept = self.selected()
        # Only a row the operator picked follows them into the next list. One
        # the dialog showed for being the only candidate is let go.
        keep_key = kept.chip.key if kept is not None and not self._auto_picked else None

        pick: QTreeWidgetItem | None = None
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
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
            self._auto_picked = pick is None and self.tree.topLevelItemCount() == 1
            if self._auto_picked:
                pick = self.tree.topLevelItem(0)  # nothing to choose between
            self.tree.setCurrentItem(pick)
        finally:
            self.tree.blockSignals(False)
        self._on_selection()
        self._say_result()

        pending, self._pending_move = self._pending_move, 0
        if pending and found is not None:
            self._step(pending)

    def _say_result(self) -> None:
        """The lookup line, for the list as it stands."""
        text, total = self._wanted, self._total
        if self._failed:
            self._say("The lookup failed — the log has the reason. Enter tries again.", "error")
        elif not text:
            self._say(PROMPT)
        elif not total:
            self._say(f"No chip matches “{text}”. A part number has to be typed in full.")
        elif total > MAX_SHOWN:
            self._say(f"Showing the best {MAX_SHOWN:,} of {total:,} candidates — keep typing.")
        elif total == 1:
            self._say("1 candidate.")
        else:
            self._say(f"{total:,} candidates, best match first.")

    def _say(self, text: str, tone: str | None = None) -> None:
        self.status_label.setText(text)
        set_tone(self.status_label, tone)

    # --- the picked candidate -------------------------------------------------

    def selected(self) -> Candidate | None:
        """The candidate picked in the list, if one is."""
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)

    def _on_operator_pick(self) -> None:
        """The pick moved by key or mouse — not by a list being rebuilt."""
        self._auto_picked = False
        self._on_selection()

    def _on_selection(self) -> None:
        candidate = self.selected()
        key = None if candidate is None else candidate.chip.key
        if key != self._detail_key:
            self._web_said = None  # what was said was about another chip
            if not self._opening:
                self._say_open("")
        self._detail_key = key

        self.details.clear()
        for label in (self.title_label, self.facts_label, self.description_label):
            self._set_line(label, "")
        if candidate is None:
            waiting = self.tree.topLevelItemCount() > 0
            can_open = self._open_document is not None
            not_picked = NOT_PICKED_ENTER if can_open else NOT_PICKED
            self._set_line(self.found_label, not_picked if waiting else "")
        else:
            try:
                self._show_chip(candidate)
            except Exception:  # a record the pane can't draw: not the HUD's problem
                log.exception("chip finder: could not show a chip's details")
                self.details.clear()
                self._set_line(
                    self.found_label, "This chip's details can't be shown — the log has the reason."
                )
        self._refresh_open_button()
        self._refresh_web()

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
            mono_columns=(0, 1),
        )
        self._section(
            "Documents",
            [
                (
                    (d.kind, d.filename, _document_facts(d)),
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
        mono_columns: tuple[int, ...] = (),
    ) -> None:
        """One titled group in the detail list; an empty one says so.

        A row's tooltip is the dialog's own words, never the chip's: Qt
        renders a tooltip that looks like markup as markup.
        """
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
            for column in mono_columns:
                row.setFont(column, self._mono)
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
            # Typed in, not set: undo in the box goes back to the chip being read.
            self.query_edit.setFocus()
            self.query_edit.selectAll()
            self.query_edit.insert(data.mpn)
            self.lookup_now()

    # --- opening a document ---------------------------------------------------

    def _cant_open(self, chip: Chip | None) -> str | None:
        """Why there's nothing to open right now, or ``None`` when there is.

        The dialog's own words only — this is also the button's tooltip.
        """
        if self._open_document is None:
            return self._open_unavailable
        if chip is None:
            return "Pick a candidate first."
        if not chip.documents:
            return "No documents are listed for this chip."
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

        ``False`` when nothing was started — nothing picked, nothing to open
        it with, no documents listed — with the reason on the dialog.
        ``True`` means the opener is running; what it did arrives by signal.
        """
        candidate = self.selected()
        return self._open(None if candidate is None else candidate.chip, None)

    def _open(self, chip: Chip | None, document: Document | None) -> bool:
        reason = self._cant_open(chip)
        opener = self._open_document
        if reason is not None or opener is None or chip is None:
            self._say_open(reason or self._open_unavailable, "warning")
            return False
        self._opening = True
        self._opening_from = self.query_edit.text()
        self._refresh_open_button()
        self._say_open(f"Looking for {_what(chip, document)}…")
        if not self._run(lambda: self._do_open(opener, chip, document)):
            self._on_opened(chip, document, RuntimeError("no worker"))
            return False
        return True

    def _do_open(self, opener: OpenDocument, chip: Chip, document: Document | None) -> None:
        """Find and open the file. Runs off the GUI thread.

        Whatever the opener does, an answer goes back — without one the
        button would stay off for good.
        """
        outcome: object
        try:
            outcome = opener(chip, document)
        except BaseException as exc:
            log.exception("chip finder: opening %s failed", _what(chip, document))
            outcome = exc
        with contextlib.suppress(RuntimeError):  # the dialog went while it ran
            self._opened.emit(chip, document, outcome)

    def _on_opened(self, chip: Chip, document: Document | None, outcome: object) -> None:
        self._opening = False
        self._refresh_open_button()
        what = _what(chip, document)
        if isinstance(outcome, BaseException):
            self._say_open(f"Couldn't open {what} — the log has the reason.", "error")
            return
        result = outcome if isinstance(outcome, OpenOutcome) else OpenOutcome(bool(outcome))
        if not result.opened:
            self._say_open(result.detail or f"No file found for {what}.", "warning")
            return
        self._say_open(result.detail or f"Opened {what}.", "ok")
        if self.query_edit.text() == self._opening_from:
            self.query_edit.selectAll()  # done with this one: the next mark types over it

    # --- linking out ------------------------------------------------------------

    def _may_link_out(self) -> bool:
        """Whether an address may go to the browser right now.

        Asked fresh every time, never remembered: an engagement can start
        while the window is open. No one to ask, or an answer that raises, is
        a no.
        """
        if self._link_out_allowed is None:
            return False
        try:
            return bool(self._link_out_allowed())
        except Exception:
            log.exception("chip finder: asking whether to link out failed")
            return False

    def _locked_reason(self) -> str:
        return LINK_OUT_UNWIRED if self._link_out_allowed is None else self._link_out_locked

    def _makers_page(self) -> tuple[WebAddress | None, str]:
        """The address **Maker's page** would open, or why there isn't one.

        The document picked in the detail list when one is; otherwise the
        first of the chip's documents that says where its maker has it.
        """
        candidate = self.selected()
        if candidate is None:
            return None, "Pick a candidate first."
        item = self.details.currentItem()
        row = None if item is None else item.data(0, Qt.ItemDataRole.UserRole)
        documents = [row] if isinstance(row, Document) else list(candidate.chip.documents)
        listed = False
        for document in documents:
            address = document_address(document)
            if address is not None:
                return address, ""
            listed = listed or bool(getattr(document, "url", None))
        return None, REFUSED_ADDRESS if listed else NO_MAKERS_PAGE

    def _refresh_web(self) -> None:
        """Redraw the link-out buttons and their line. Builds addresses; opens nothing."""
        allowed = self._may_link_out()
        locked = self._locked_reason()
        typed = normalise_mark(self.query_edit.text())
        mark = sendable(typed)
        for site, button in zip(MARKING_SITES, self.site_buttons, strict=True):
            address = site.address(typed) if allowed else None
            button.setEnabled(address is not None)
            if not allowed:
                tip = locked
            elif not typed:
                tip = "Type a mark first."
            elif mark is None:
                tip = NOT_SENDABLE
            else:
                tip = site.does if address is not None else site.cant
            button.setToolTip(tip)  # the dialog's own words: never what's typed

        page, why_not = self._makers_page()
        self.maker_button.setEnabled(allowed and page is not None)
        if not allowed:
            self.maker_button.setToolTip(locked)
        elif page is None:
            self.maker_button.setToolTip(why_not)
        else:
            # The host passed linkout's check: letters, digits, dots, hyphens.
            self.maker_button.setToolTip(
                f"Opens {page.host} in your browser — where this document's maker publishes it."
            )

        if self._web_said is not None:
            text, tone = self._web_said
        elif not allowed:
            text, tone = locked, "warning"
        elif not typed:
            text, tone = WEB_PROMPT, None
        elif mark is None:
            text, tone = NOT_SENDABLE, None
        else:
            text, tone = f"Look “{mark}” up on the web:", None
        self.web_label.setText(text)
        set_tone(self.web_label, tone)

    def _say_web(self, text: str, tone: str | None) -> None:
        self._web_said = (text, tone)
        self._refresh_web()

    def search_on(self, site: MarkingSite) -> bool:
        """Look what's typed up on ``site``, in the operator's browser.

        Only a click gets here. ``False`` when nothing was opened, with the
        reason on the dialog.
        """
        if not self._may_link_out():
            self._say_web(self._locked_reason(), "warning")
            return False
        typed = normalise_mark(self.query_edit.text())
        address = site.address(typed)
        if address is None:
            self._say_web(site.cant if sendable(typed) else NOT_SENDABLE, "warning")
            return False
        return self._hand_over(address)

    def open_makers_page(self) -> bool:
        """Open the picked document where its maker publishes it.

        Only a click gets here. ``False`` when nothing was opened, with the
        reason on the dialog.
        """
        if not self._may_link_out():
            self._say_web(self._locked_reason(), "warning")
            return False
        address, why_not = self._makers_page()
        if address is None:
            self._say_web(why_not, "warning")
            return False
        return self._hand_over(address)

    def _hand_over(self, address: WebAddress) -> bool:
        try:
            taken = bool(self._open_url(address.url))
        except Exception:
            log.exception("chip finder: handing %s to the browser failed", address.host)
            taken = False
        if taken:
            self._say_web(f"Handed {address.host} to your browser.", "ok")
        else:
            self._say_web(f"Couldn't hand {address.host} to a browser.", "error")
        return taken

    def _say_open(self, text: str, tone: str | None = None) -> None:
        if self._open_document is None:
            return  # the line already holds why nothing opens, and keeps it
        self.open_label.setText(text)
        set_tone(self.open_label, tone)


def _document_facts(document: Document) -> str:
    """A document row's last column: pages, its note, and where its maker has it."""
    pages = _plural(document.pages, "page") if document.pages else ""
    address = document_address(document)
    at = f"at {address.host}" if address is not None else ""
    return " — ".join(part for part in (pages, document.note or "", at) if part)


def _what(chip: Chip, document: Document | None) -> str:
    return document.filename if document is not None else f"the datasheet for {chip.mpn}"
