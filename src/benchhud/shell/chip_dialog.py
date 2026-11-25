"""Find chip… — from the mark on a chip, or its part number, to what it is.

Used with a board under the microscope, so it's built for the keyboard: type
what's printed on the part, arrow to a candidate, Enter opens its datasheet.

**One box, tried both ways.** What's typed goes to the source as a mark and as
a part number (:func:`benchhud.chips.find.find_chips`). The answers are one
list: the chip whose part number it is comes first, then every chip whose mark
the text holds whole, then everything still being typed — marks and the
starts of part numbers, least left to type first. A mark is found from its
first character; the start of a part number from the third.

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
the browser by a mouse click on a button that says where it goes — a site
button is named for its host; **Maker's page** names it in its tooltip and
on the document's row. Never as you type, and never from the keyboard: those
buttons don't take the focus, so no key can reach them. Only what's in the
box at that moment, or the document's own address, goes with it. The caller
says whether linking out is allowed right now, the way it supplies the
datasheet opener; the main window answers no while a recon engagement is
active. **With nothing to ask, the answer is no**: a button that's wrongly off
costs a trip to the browser by hand, one that's wrongly on names a client's
part to a stranger. Only a plain ``True`` is a yes, and the question is asked
again at every click. benchhud itself still makes no network request.

**Which source said what.** When the chip data is several sources put
together, the detail pane says which one each value came from, and where two
disagree it lists every source's value, the one shown first. With a single
source there's nothing to tell apart and the pane is as it always was. This
is an operator dialog, so source names and the values that lost are fine here.

**It follows Preferences.** One dialog is kept and shown again, so the text
and the pick survive a trip back to the board. When the sources or the
datasheet folder change, the main window hands it the new ones
(:meth:`FindChipDialog.set_source`, :meth:`FindChipDialog.set_open_document`)
and what's typed is looked up again — an answer still on its way from the old
source is dropped.

**Putting a chip on the stream card is a click, and only its part number
goes.** The Chip panel is stream output. When the caller hands over a way to
send a chip there, the picked candidate gets a **Show on stream card** button:
a mouse click, never Enter and never a side effect of picking a row. What's
handed over is the part number and nothing else — the card looks the chip up
for itself, so nothing from this pane (a note, a source's name, a value that
lost) can ride along. Without that callable, or with no chip data, there's no
button at all.

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

from benchhud.chips.card import NOTHING_TO_SHOW, SHOW_ON_CARD, SHOW_ON_CARD_TIP, sent_line
from benchhud.chips.composite import MergedChip
from benchhud.chips.find import MIN_PART_NUMBER_START, Candidate, find_chips, with_provenance
from benchhud.chips.linkout import (
    MARKING_SITES,
    MAX_SENT,
    MarkingSite,
    WebAddress,
    document_address,
    sendable,
)
from benchhud.chips.marking import MatchKind, normalise_mark
from benchhud.chips.model import Chip, Document, Substitute, mpn_key
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
#: click and whenever the buttons are redrawn, so it has to be quick. Only
#: ``True`` itself is a yes: a slip that hands back some other truthy thing —
#: the engagement's ref, say — must not unlock anything.
LinkOutAllowed = Callable[[], bool]
#: Hands an address to the operator's browser; true when it was taken.
OpenUrl = Callable[[str], object]
#: Puts the chip with this part number on the stream card. Given the part
#: number and nothing else. Called on the GUI thread; it has to return at once.
ShowOnCard = Callable[[str], object]

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

NO_DATA = (
    "No chip data is configured, so there's nothing to look up."
    " Add a source in Preferences → Chips."
)
NO_OPENER = "No datasheet folder is set up, so there's nothing to open."
PROMPT = "Type the mark printed on the chip, or its part number."
NOT_PICKED = "Nothing picked — ↓ moves through the candidates."
NOT_PICKED_ENTER = NOT_PICKED[:-1] + ", Enter opens the picked one's datasheet."

LINK_OUT_LOCKED = (
    "Web lookups are off while a recon engagement is active — "
    "they'd tell an outside site what's on the bench."
)
LINK_OUT_UNWIRED = "Web lookups are off: nothing has told this window they're allowed."
LINK_OUT_UNKNOWN = "Web lookups are off: whether they're allowed couldn't be checked."
WEB_PROMPT = "Type a mark to look it up on the marking sites."
NOT_SENDABLE = f"Only a plain mark, of {MAX_SENT} characters at most, is sent to a marking site."
NO_MAKERS_PAGE = "No maker's page is listed for this."
REFUSED_ADDRESS = "The address listed isn't a plain https:// one, so it isn't opened."

_COLUMNS = ("Part number", "Maker", "Package", "Functions", "Mark", "Match")
_MARK_COLUMN = 4

#: Columns in the detail list: three of data, then which sources have the row.
_DETAIL_COLUMNS = 4
_SOURCE_COLUMN = 3

#: A chip's single values, as the detail pane names them, in the order shown.
_FIELD_NAMES = {
    "manufacturer": "maker",
    "family": "family",
    "package": "package",
    "package_family": "package family",
    "description": "description",
}

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
    elif candidate.by_part_number_start:
        parts.append("start of part number")
    if candidate.match is not None:
        kind = candidate.match.kind
        parts.append(_KIND_LABELS.get(kind, str(kind.value)))
    return " + ".join(parts)


def _too_short(text: str) -> str:
    """Why a part number might not have been found yet, when that's the reason."""
    if len(mpn_key(text)) >= MIN_PART_NUMBER_START:
        return ""
    return f" The start of a part number is looked up from {MIN_PART_NUMBER_START} characters."


def _plural(count: int, one: str) -> str:
    return f"{count:,} {one if count == 1 else one + 's'}"


class _Answer(list):
    """A lookup's candidates, tagged with which source they were found in.

    The tag is :attr:`FindChipDialog._round` when the lookup started. If the
    source was swapped while it ran, the tag no longer matches and the answer
    is dropped. A lookup that failed is an empty one with ``failed`` set — it
    carries its tag too, so an old source's failure isn't pinned on the new.
    """

    round = 0
    failed = False


def provenance_line(merged: MergedChip) -> str:
    """Which source each single value came from, in a sentence.

    ``From local: package  ·  from community: maker, description``. The part
    number isn't listed: it's the chip's identity, the same in every source.
    """
    by_source: dict[str, list[str]] = {}
    for name, source in merged.scalar_sources:
        label = _FIELD_NAMES.get(name)
        if label is not None:
            by_source.setdefault(source, []).append(label)
    parts = [f"from {source}: {', '.join(labels)}" for source, labels in by_source.items()]
    line = "  ·  ".join(parts)
    line = line[:1].upper() + line[1:]
    if merged.conflicts:
        disputed = ", ".join(_FIELD_NAMES.get(c.field, c.field) for c in merged.conflicts)
        line = f"{line}.  Sources disagree on {disputed} — every value is listed below."
    return line


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

    ``link_out_allowed`` answers "may this window link out right now?"; left
    out, link-outs are off. ``open_url`` hands an address to the browser and
    defaults to :func:`benchhud.shell.browser.open_in_browser`.

    ``show_on_card`` puts a chip on the stream card, given its part number.
    Left out, there is no such button.

    ``open_unavailable`` and ``link_out_locked`` are the caller's own words
    for why something is off. They're shown in tooltips as well as on the
    dialog, so they're trusted the way the dialog's own strings are: no
    markup, and nothing taken from a data file.
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
        open_url: OpenUrl | None = None,
        show_on_card: ShowOnCard | None = None,
        executor: Executor = _thread_executor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find chip")
        self.resize(860, 800)
        self._source = source
        if configured is None:
            configured = not isinstance(source, NullChipSource)
        self._configured = configured
        # Goes up when the source is swapped, so an answer from the old one
        # isn't taken for an answer from the new.
        self._round = 0
        self._reread = False  # the next lookup has the source read its folders first
        self._restore_key: str | None = None  # the pick to make again after a swap
        self._open_document = open_document
        self._open_unavailable = open_unavailable
        self._link_out_allowed = link_out_allowed
        self._link_out_locked = link_out_locked or LINK_OUT_LOCKED
        self._link_out_unknown = False  # the last time it was asked, the asking failed
        self._open_url = open_url if open_url is not None else open_in_browser
        self._show_on_card = show_on_card
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
        self.query_edit.setPlaceholderText("Mark on the chip, or its part number")
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
        # Which source each value came from — only when there are several.
        self.sources_label = _data_label(hint(QLabel()))
        self.found_label = _data_label(hint(QLabel()))
        for label in (
            self.title_label,
            self.facts_label,
            self.description_label,
            self.sources_label,
            self.found_label,
        ):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.details = QTreeWidget()
        self.details.setColumnCount(_DETAIL_COLUMNS)
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
        detail_layout.addWidget(self.sources_label)
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
        self._show_open_state()

        # The link-outs: one button per marking site, named for the host it
        # opens, and a line that says what would be sent — or why they're off.
        self.web_label = _data_label(QLabel())
        self.site_buttons: list[QPushButton] = []
        web_row = QHBoxLayout()
        for site in MARKING_SITES:
            site_button = QPushButton(site.host)
            site_button.clicked.connect(lambda _checked=False, site=site: self.search_on(site))
            self.site_buttons.append(site_button)
            web_row.addWidget(site_button)
        web_row.addStretch(1)
        self.maker_button = QPushButton("Maker's page")
        self.maker_button.clicked.connect(self.open_makers_page)
        for link_button in (*self.site_buttons, self.maker_button):
            # A link-out takes the mouse. These never hold the focus, so no
            # key reaches them — not Space, not whatever a desktop theme
            # counts as "press" — and a click leaves the focus in the box,
            # where the next mark is typed. Nor can one be the dialog's
            # default, which Enter would press.
            link_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            link_button.setAutoDefault(False)
            link_button.setDefault(False)

        # What became of sending a chip to the stream card, until the pick moves on.
        self.card_label = _data_label(QLabel())
        self.card_label.setVisible(False)
        self.card_button = QPushButton(SHOW_ON_CARD)
        self.card_button.clicked.connect(self.show_selected_on_card)
        # Like a link-out, this leaves the operator's own screen: it takes a
        # click. It never holds the focus, so neither Enter nor Space reaches it.
        self.card_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.open_button = QPushButton("Open datasheet")
        self.open_button.clicked.connect(self.open_selected)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(self.open_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.card_button, QDialogButtonBox.ButtonRole.ActionRole)
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
        layout.addWidget(self.card_label)
        layout.addWidget(self.web_label)  # a row of its own: it never loses its width
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

    # --- following a change in Preferences ------------------------------------

    def set_source(
        self, source: ChipSource, *, configured: bool | None = None, reread: bool = False
    ) -> None:
        """Look things up in ``source`` from now on.

        What's listed came from the old source, so the list is emptied at once
        — nothing from a source that's gone stays there to be opened — and
        what's typed is looked up again. The row the operator had picked is
        picked again if the new answer has it. An answer the old source is
        still working on is dropped when it arrives, whether it found
        something or failed.

        ``reread`` is for the same source whose folders may have changed (a
        Preferences OK with the list untouched): the next lookup has it read
        them again first, on the worker, so the list can't come back with
        what the source held before.

        ``configured`` means what it does in the constructor.
        """
        if configured is None:
            configured = not isinstance(source, NullChipSource)
        kept = self.selected()
        if kept is not None and not self._auto_picked:
            self._restore_key = kept.chip.key
        self._source = source
        self._configured = configured
        self._round += 1
        self._reread = reread
        self.notice_label.setVisible(not configured)
        self._debounce.stop()
        self._slow.stop()  # "Looking up…" was about the old source
        pending, self._pending_move = self._pending_move, 0
        self._show("", [])  # the old source's rows go now
        if not configured:
            self._restore_key = None
            self._say("")  # the notice says why there's nothing to look up
            return
        if not self._wanted:
            self._restore_key = None
            return
        self._pending_move = pending  # an arrow already pressed lands on the new list
        self._shown = None  # nothing listed answers this source yet
        self._say(f"Looking up “{self._wanted}”…")
        self._dispatch()

    def set_open_document(
        self, open_document: OpenDocument | None, open_unavailable: str = NO_OPENER
    ) -> None:
        """Open documents with ``open_document`` from now on; ``None`` turns it off.

        An opener that's already running finishes. What it says is shown —
        unless opening has been turned off meanwhile, in which case the line
        holds why, since that's what's true from then on. A message already
        showing ("Opened …") and the picked row are left alone.
        """
        was_off = self._open_document is None
        self._open_document = open_document
        self._open_unavailable = open_unavailable
        if not self._opening and (open_document is None or was_off):
            self._show_open_state()  # on→off: the reason; off→on: the reason goes
        self._retip_documents()
        self._refresh_open_button()

    def _retip_documents(self) -> None:
        """Have the document rows say what Enter does now, without redrawing them."""
        tip = "Enter opens this document." if self._open_document else self._open_unavailable
        for i in range(self.details.topLevelItemCount()):
            head = self.details.topLevelItem(i)
            for j in range(head.childCount()):
                row = head.child(j)
                if isinstance(row.data(0, Qt.ItemDataRole.UserRole), Document):
                    for column in range(_DETAIL_COLUMNS):
                        row.setToolTip(column, tip)

    def refresh_link_outs(self) -> None:
        """Ask again whether link-outs are allowed, and redraw them to match.

        For the main window to call the moment an engagement becomes active:
        the buttons would refuse a click anyway, but they shouldn't sit there
        looking live.
        """
        self._refresh_web()

    def _show_open_state(self) -> None:
        """The open line at rest: empty, or why nothing can be opened."""
        off = self._open_document is None
        self.open_label.setObjectName("hint" if off else "")
        self.open_label.setText(self._open_unavailable if off else "")
        set_tone(self.open_label, None)
        self.open_label.style().unpolish(self.open_label)
        self.open_label.style().polish(self.open_label)

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
        source, asked_in = self._source, self._round
        reread, self._reread = self._reread, False
        self._looking = True
        self._slow.start()
        if not self._run(lambda: self._look_up(text, source, asked_in, reread)):
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

    def _look_up(
        self,
        text: str,
        source: ChipSource | None = None,
        asked_in: int | None = None,
        reread: bool = False,
    ) -> None:
        """The lookup itself. Runs off the GUI thread.

        Whatever the source does, an answer goes back — a lookup that never
        answered would hold every later one up behind it. Where each value
        came from is fetched here too, for the candidates that will be shown,
        so picking one never sends the GUI thread back to the source.
        """
        source = self._source if source is None else source
        found = _Answer()
        try:
            if reread:
                again = getattr(source, "reload", None)
                if callable(again):
                    again()
            found = _Answer(with_provenance(source, find_chips(source, text), MAX_SHOWN))
        except BaseException:
            log.exception("chip finder: looking up %r failed", text)
            found.failed = True
        found.round = self._round if asked_in is None else asked_in
        with contextlib.suppress(RuntimeError):  # the dialog went while it ran
            self._looked_up.emit(text, found)

    def _on_slow(self) -> None:
        if not self._configured:
            return
        if self._looking and self._wanted and self._wanted != self._shown:
            self._say(f"Looking up “{self._wanted}”…")

    def _on_looked_up(self, text: str, found: list[Candidate] | None) -> None:
        self._looking = False
        self._slow.stop()
        if getattr(found, "round", self._round) != self._round:
            # From a source that's since been swapped — found or failed, it's
            # not an answer any more.
            self._dispatch()
            return
        if getattr(found, "failed", False):
            found = None
        if text == self._wanted:
            try:
                self._show(text, found)
            except Exception:  # a record the list can't draw: not the HUD's problem
                log.exception("chip finder: could not show the answer for %r", text)
                self._show(text, None)
            self._restore_key = None  # used or not, it was for this answer only
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
        if found and self._restore_key is not None:
            # The pick from before the source was swapped, if it's still listed.
            keep_key, self._restore_key = self._restore_key, None

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
            self._say(f"No chip matches “{text}”.{_too_short(text)}")
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
            self._say_card("")  # what was sent was another chip
            self._web_said = None  # what was said was about another chip
            if not self._opening:
                self._say_open("")
        self._detail_key = key

        self.details.clear()
        self._show_source_column(False)
        for label in (
            self.title_label,
            self.facts_label,
            self.description_label,
            self.sources_label,
        ):
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
        self._refresh_card_button()
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

        # Several sources put together: say which one each part came from.
        merged = candidate.merged
        self._set_line(self.sources_label, provenance_line(merged) if merged else "")
        self._show_source_column(merged is not None)

        def behind(name: str, count: int) -> list[str]:
            """Per row of a list, the sources that have it — blank with one source."""
            sources = merged.sources_of(name) if merged is not None else ()
            if len(sources) != count:
                return [""] * count
            return [", ".join(names) for names in sources]

        if merged is not None and merged.conflicts:
            self._section(
                "Sources disagree",
                [
                    (
                        (
                            _FIELD_NAMES.get(conflict.field, conflict.field),
                            value,
                            "shown" if place == 0 else "",
                            source,
                        ),
                        None,
                        "",
                    )
                    for conflict in merged.conflicts
                    for place, (source, value) in enumerate(conflict.values)
                ],
            )

        can_open = self._open_document is not None
        marked = behind("markings", len(chip.markings))
        self._section(
            "Markings",
            [
                ((m.code, ", ".join(m.examples), m.note or "", source), None, "")
                for m, source in zip(chip.markings, marked, strict=True)
            ],
            mono_columns=(0, 1),
        )
        listed = behind("documents", len(chip.documents))
        self._section(
            "Documents",
            [
                (
                    (d.kind, d.filename, _document_facts(d), source),
                    d,
                    "Enter opens this document." if can_open else self._open_unavailable,
                )
                for d, source in zip(chip.documents, listed, strict=True)
            ],
        )
        swaps = behind("substitutes", len(chip.substitutes))
        self._section(
            "Substitutes",
            [
                ((s.mpn, s.note or "", "", source), s, "Enter looks this part number up.")
                for s, source in zip(chip.substitutes, swaps, strict=True)
            ],
        )
        seen = behind("used_in", len(chip.used_in))
        self._section(
            "Seen on",
            [
                ((u.board, u.designator or "", u.role or "", source), None, "")
                for u, source in zip(chip.used_in, seen, strict=True)
            ],
        )

    def _show_source_column(self, shown: bool) -> None:
        """The fourth detail column, or — with one source — the list as it always was.

        Without it the third column is the last and takes the spare width,
        exactly as before there was a fourth. With it, the third still takes
        the spare width and the sources sit at their own width beside it.
        """
        header = self.details.header()
        self.details.setColumnHidden(_SOURCE_COLUMN, not shown)
        header.setStretchLastSection(not shown)
        header.setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch if shown else QHeaderView.ResizeMode.ResizeToContents
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
        elif candidate.by_part_number_start:
            parts.append("Found by the start of its part number.")
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
        rows: list[tuple[tuple[str, str, str, str], object, str]],
        *,
        mono_columns: tuple[int, ...] = (),
    ) -> None:
        """One titled group in the detail list; an empty one says so.

        A row is three columns of data and a fourth naming the sources that
        have it (blank when there's one source). A row's tooltip is the
        dialog's own words, never the chip's: Qt renders a tooltip that looks
        like markup as markup.
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
                for column in range(_DETAIL_COLUMNS):
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
        if self._open_document is None:
            # Opening was turned off while this ran: the line says why it's
            # off, not "Looking for…" for ever.
            self._show_open_state()
            return
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

    # --- the stream card ----------------------------------------------------------

    def _refresh_card_button(self) -> None:
        """There only when there's somewhere to send a chip and chips to send."""
        self.card_button.setVisible(self._show_on_card is not None and self._configured)
        picked = self.selected() is not None
        self.card_button.setEnabled(picked)
        self.card_button.setToolTip(SHOW_ON_CARD_TIP if picked else NOTHING_TO_SHOW)

    def show_selected_on_card(self) -> bool:
        """Put the picked chip on the stream card. True when it was handed over.

        The part number is all that's handed over: the card looks the chip up
        for itself and shows what a card shows.
        """
        candidate, send = self.selected(), self._show_on_card
        if candidate is None or send is None or not self._configured:
            return False
        mpn = candidate.chip.mpn
        try:
            send(mpn)
        except Exception:
            log.exception("chip finder: could not put a chip on the stream card")
            self._say_card("Couldn't put it on the Chip panel — the log has the reason.", "error")
            return False
        self._say_card(sent_line(mpn), "ok")
        return True

    def _say_card(self, text: str, tone: str | None = None) -> None:
        self.card_label.setText(text)
        self.card_label.setVisible(bool(text))
        set_tone(self.card_label, tone)

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
            answer = self._link_out_allowed()
        except Exception:
            if not self._link_out_unknown:  # once, not at every keystroke
                log.exception("chip finder: asking whether to link out failed")
            self._link_out_unknown = True
            return False
        self._link_out_unknown = False
        return answer is True

    def _locked_reason(self) -> str:
        if self._link_out_allowed is None:
            return LINK_OUT_UNWIRED
        return LINK_OUT_UNKNOWN if self._link_out_unknown else self._link_out_locked

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

        if not allowed:
            # Before anything else, and over anything said earlier: while
            # link-outs are off, the line says so.
            self._web_said = None
            text, tone = locked, "warning"
        elif self._web_said is not None:
            text, tone = self._web_said
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

        Called by that site's button and nothing else. ``False`` when
        nothing was opened, with the reason on the dialog.
        """
        if not self._may_link_out():
            self._refresh_web()  # the buttons go off and the line says why
            return False
        typed = normalise_mark(self.query_edit.text())
        address = site.address(typed)
        if address is None:
            self._say_web(site.cant if sendable(typed) else NOT_SENDABLE, "warning")
            return False
        return self._hand_over(address)

    def open_makers_page(self) -> bool:
        """Open the picked document where its maker publishes it.

        Called by the **Maker's page** button and nothing else. ``False``
        when nothing was opened, with the reason on the dialog.
        """
        if not self._may_link_out():
            self._refresh_web()  # the buttons go off and the line says why
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
