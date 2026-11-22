"""Chip reference panel — one chip from the chip reference, readable on a stream.

A card for the part on the bench: its part number, who makes it, its package,
what it does, the marks printed on it and a line of description. It sits beside
the pinout crib and is the same kind of thing — reference material a viewer can
read along with.

**Stream-safe (not sensitive).** Everything drawn here is either fixed wording
or a value from a :class:`~benchhud.chips.model.Chip`, which has no job,
customer or engagement field. The panel is handed a chip source and nothing
else: it can't reach a job, a ticket or an engagement, and it never echoes the
text it was asked to look up — only what the chip reference says back.

A chip record still holds things that don't belong on air, so the card shows
part of it (:data:`SHOWN_FIELDS`) and leaves the rest out
(:data:`LEFT_OUT_FIELDS`):

- **Notes** — a marking's, a document's, a substitute's. A private overlay is
  the operator's own data, and a note is where "the one from the grey donor
  board" gets written.
- **Board sightings** (``used_in``). A board number says what's been on this
  bench.
- **Documents.** A filename or a URL out of someone's datasheet folder; and
  opening one is an action, which belongs to the Find chip dialog.
- **Substitutes.** Each one is advice with a caveat in its note; without the
  note it's a part number that reads like a recommendation.
- **Marking examples.** Whole marks read off real parts, lot codes and all.
  The code is the part that identifies the chip, and that's shown.
- **Credits** (``sources``) and **which configured source said what.** A
  source's name is a label out of the operator's config.

When configured sources disagree about a value, the card says *which values*
are disputed — "Sources disagree on package" — so nobody reads a contested
value out as fact. It doesn't name the sources or show the losing values.

Every value is drawn as plain text, shortened to fit: a record can't smuggle
markup, an image path or a wall of text onto the card.

The lookup runs on a worker thread, because a source's first read can take a
moment. Each lookup carries a ticket, and an answer whose ticket is no longer
the current one is dropped — so a slow answer for a chip the operator has
moved on from never lands.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from benchhud.chips.model import Chip
from benchhud.chips.source import ChipSource, NullChipSource
from benchhud.shell.panel import PANEL_MARGINS, Panel
from benchhud.shell.qt_util import heading_font, hint, notice

log = logging.getLogger(__name__)

ChipSourceProvider = Callable[[], ChipSource]
#: Runs a job off the GUI thread.
Executor = Callable[[Callable[[], None]], None]

#: The :class:`~benchhud.chips.model.Chip` fields the card draws. Of a
#: marking, only its ``code``.
SHOWN_FIELDS = frozenset(
    {"mpn", "manufacturer", "family", "package", "functions", "description", "markings"}
)
#: The fields it never draws — see the module docstring for why each is out.
#: ``package_family`` is only the package again, shorter.
LEFT_OUT_FIELDS = frozenset({"package_family", "sources", "documents", "substitutes", "used_in"})

#: How much of a record fits a card a viewer can read. Past these it's cut
#: with an ellipsis, or counted ("+2 more").
MAX_LINE = 48
MAX_DESCRIPTION = 180
MAX_FUNCTIONS = 4
MAX_MARKINGS = 6
MAX_MARKING = 16
#: The most the part-number box takes.
MAX_ASKED = 100

#: How a disputed field is named on the card. A field that isn't here is
#: never named — the card says "a value" rather than print what it was given.
_DISPUTED = {
    "manufacturer": "maker",
    "family": "family",
    "package": "package",
    "description": "description",
}

_NO_DATA = "No chip data configured — see Preferences → Chips."
_NO_CHIP = "No chip chosen."
_LOOKING = "Looking up…"
_NOT_FOUND = "No chip with that part number in the chip data."
_FAILED = "The chip data couldn't be read — see the log."


class State(Enum):
    """What the panel is showing."""

    NO_DATA = "no-data"
    NO_CHIP = "no-chip"
    LOOKING = "looking"
    NOT_FOUND = "not-found"
    FAILED = "failed"
    SHOWN = "shown"


def _short(text: object, limit: int) -> str:
    """``text`` on one line, cut to ``limit`` characters with an ellipsis.

    Anything that isn't a string is nothing: a mangled record draws a blank,
    not its ``repr``.
    """
    if not isinstance(text, str):
        return ""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _listed(values: Iterable[str], most: int) -> str:
    """Up to ``most`` values, comma-separated, then how many more there are."""
    kept = [value for value in values if value]
    shown = ", ".join(kept[:most])
    return f"{shown}  +{len(kept) - most} more" if len(kept) > most else shown


@dataclass(frozen=True)
class ChipCard:
    """The words on the card — every one from a chip, or fixed. No Qt."""

    title: str
    maker: str = ""
    package: str = ""
    functions: str = ""
    markings: str = ""
    description: str = ""
    disputed: str = ""


def card_for(chip: Chip, disputed: Iterable[str] = ()) -> ChipCard:
    """The card for ``chip``. ``disputed`` names the fields its sources disagree on."""
    maker = _short(chip.manufacturer, MAX_LINE)
    family = _short(chip.family, MAX_LINE)
    if family:
        maker = f"{maker} · {family} family" if maker else f"{family} family"
    functions = chip.functions if isinstance(chip.functions, tuple) else ()
    markings = chip.markings if isinstance(chip.markings, tuple) else ()
    return ChipCard(
        title=_short(chip.mpn, MAX_LINE),
        maker=maker,
        package=_short(chip.package, MAX_LINE),
        # A slug reads better on air without its hyphens: "buck controller".
        functions=_listed(
            (_short(slug, MAX_LINE).replace("-", " ") for slug in functions), MAX_FUNCTIONS
        ),
        markings=_listed(
            (_short(getattr(marking, "code", None), MAX_MARKING) for marking in markings),
            MAX_MARKINGS,
        ),
        description=_short(chip.description, MAX_DESCRIPTION),
        disputed=_disputed_line(disputed),
    )


def _disputed_line(fields: Iterable[str]) -> str:
    names: list[str] = []
    for field in fields:
        name = _DISPUTED.get(field, "a value") if isinstance(field, str) else "a value"
        if name not in names:
            names.append(name)
    return f"Sources disagree on {', '.join(names)}." if names else ""


@dataclass(frozen=True)
class Found:
    """What a lookup came back with: the chip, and the fields in dispute."""

    chip: Chip
    disputed: tuple[str, ...] = ()


def look_up(source: ChipSource, mpn: str) -> Found | None:
    """The chip with this part number — merged, when the source merges several.

    Runs on a worker. ``None`` is "no such chip". Raises if the source hands
    back something that isn't a chip; the caller reports that, never draws it.
    """
    merged = getattr(source, "merged", None)
    if callable(merged):
        answer = merged(mpn)
        if answer is None:
            return None
        chip = answer.chip
        disputed = tuple(conflict.field for conflict in answer.conflicts)
    else:
        chip, disputed = source.get(mpn), ()
        if chip is None:
            return None
    if not isinstance(chip, Chip):
        raise TypeError(f"the chip source answered with a {type(chip).__name__}, not a chip")
    return Found(chip, disputed)


def _thread_executor(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="chip-panel", daemon=True).start()


def _plain(label: QLabel, *, wrap: bool = True) -> QLabel:
    """A label for a value out of a record: plain text that never widens the dock.

    Plain, so a value holding ``<img src=…>`` is drawn as those characters and
    not as the image. ``Ignored`` across, so a long value wraps or clips
    rather than forcing the dock wide (#230).
    """
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setMinimumWidth(1)
    return label


def _scaled(base: QFont, scale: float, *, bold: bool = False) -> QFont:
    font = QFont(base)
    if base.pointSizeF() > 0:
        font.setPointSizeF(base.pointSizeF() * scale)
    font.setBold(bold)
    return font


class ChipPanel(Panel):
    panel_id = "chip"
    panel_title = "Chip"
    # Stream-safe: fixed wording and Chip fields only — no job or engagement context.

    #: (ticket, a :class:`Found`, ``None`` for no such chip, or ``False`` for a failed read)
    _answered = Signal(int, object)

    def __init__(
        self,
        chip_source: ChipSourceProvider,
        *,
        executor: Executor = _thread_executor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._chip_source = chip_source
        self._executor = executor
        #: The part number asked for, as asked. Never drawn.
        self._wanted: str | None = None
        #: The current lookup's ticket; an answer with any other is dropped.
        self._ticket = 0
        self._state = State.NO_CHIP
        self._chip: Chip | None = None
        self._answered.connect(self._on_answer)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_ask_row())
        self.card = self._build_card()
        layout.addWidget(self.card)
        self.message_label = _plain(QLabel())
        self.message_label.setObjectName("ChipMessage")
        layout.addWidget(self.message_label)
        layout.addStretch(1)

        self.refresh()

    # --- construction ----------------------------------------------------

    def _build_ask_row(self) -> QHBoxLayout:
        self.part_edit = QLineEdit()
        self.part_edit.setPlaceholderText("Part number…")
        self.part_edit.setMaxLength(MAX_ASKED)
        self.part_edit.setMinimumWidth(1)
        self.part_edit.returnPressed.connect(self._ask_typed)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Take the chip off the panel")
        self.clear_button.clicked.connect(self.clear)
        row = QHBoxLayout()
        row.addWidget(self.part_edit, stretch=1)
        row.addWidget(self.clear_button)
        return row

    def _build_card(self) -> QWidget:
        card = QWidget()
        base = card.font()
        self.title_label = _plain(QLabel())
        self.title_label.setObjectName("ChipTitle")
        self.title_label.setFont(_scaled(base, 1.7, bold=True))
        self.maker_label = hint(_plain(QLabel()))

        self.package_label = _plain(QLabel())
        self.functions_label = _plain(QLabel())
        self.markings_label = _plain(QLabel())
        self.markings_label.setObjectName("ChipMarkings")
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.markings_label.setFont(_scaled(mono, 1.25, bold=True))
        for label in (self.package_label, self.functions_label):
            label.setFont(_scaled(base, 1.15))

        facts = QGridLayout()
        facts.setContentsMargins(0, 4, 0, 4)
        facts.setHorizontalSpacing(10)
        facts.setColumnStretch(1, 1)
        self._fact_rows: list[tuple[QLabel, QLabel]] = []
        rows = (
            ("Package", self.package_label),
            ("Does", self.functions_label),
            ("Marked", self.markings_label),
        )
        for row, (caption_text, value) in enumerate(rows):
            caption = hint(QLabel(caption_text))
            caption.setFont(heading_font(base))
            facts.addWidget(caption, row, 0, Qt.AlignmentFlag.AlignTop)
            facts.addWidget(value, row, 1)
            self._fact_rows.append((caption, value))

        self.description_label = _plain(QLabel())
        self.disputed_label = notice(_plain(QLabel()), "warning")

        column = QVBoxLayout(card)
        column.setContentsMargins(0, 6, 0, 0)
        column.addWidget(self.title_label)
        column.addWidget(self.maker_label)
        column.addLayout(facts)
        column.addWidget(self.description_label)
        column.addWidget(self.disputed_label)
        return card

    # --- the API ---------------------------------------------------------

    def set_chip(self, mpn: str) -> None:
        """Show the chip with this full part number. Blank is :meth:`clear`.

        Returns at once: the lookup runs on a worker and the card follows. The
        part number itself is never drawn — if the chip reference has no such
        chip the panel says so without repeating it.
        """
        wanted = " ".join(mpn.split()) if isinstance(mpn, str) else ""
        if not wanted:
            self.clear()
            return
        # What's in the box is the operator's own typing; a choice made
        # elsewhere would leave it naming a chip the card no longer shows.
        if " ".join(self.part_edit.text().split()) != wanted:
            self.part_edit.clear()
        self._wanted = wanted
        self._look_up()

    def clear(self) -> None:
        """Take the chip off the panel. A lookup still running is dropped."""
        self._wanted = None
        self.part_edit.clear()
        self._look_up()

    def refresh(self) -> None:
        """Look the chosen chip up again — call after the chip sources change."""
        self._look_up()

    @property
    def state(self) -> State:
        return self._state

    @property
    def chip(self) -> Chip | None:
        """The chip on the card, or ``None`` when the card isn't showing one."""
        return self._chip

    # --- behaviour -------------------------------------------------------

    def _ask_typed(self) -> None:
        self.set_chip(self.part_edit.text())
        self.part_edit.selectAll()

    @staticmethod
    def _no_data(source: ChipSource) -> bool:
        return isinstance(source, NullChipSource)

    def _look_up(self) -> None:
        wanted = self._wanted
        self._ticket += 1
        ticket = self._ticket
        source = self._chip_source()
        if self._no_data(source):
            self._show(State.NO_DATA)
            return
        if wanted is None:
            self._show(State.NO_CHIP)
            return
        # The old card comes down first: until the answer is in, the panel
        # shows no chip rather than the one before.
        self._show(State.LOOKING)

        def run() -> None:
            found: object = False
            try:
                found = look_up(source, wanted)
            except Exception:  # said on the panel, never raised into Qt
                log.exception("chips: the chip panel's lookup failed")
            finally:
                # The panel can be gone by the time a slow lookup answers.
                with contextlib.suppress(RuntimeError):
                    self._answered.emit(ticket, found)

        self._executor(run)

    def _on_answer(self, ticket: int, found: object) -> None:
        if ticket != self._ticket:
            return  # the choice changed while this was being looked up
        if isinstance(found, Found):
            try:
                card = card_for(found.chip, found.disputed)
            except Exception:  # a record too mangled to draw
                log.exception("chips: the chip panel couldn't draw a chip")
                self._show(State.FAILED)
                return
            self._show(State.SHOWN, found.chip, card)
        elif found is None:
            self._show(State.NOT_FOUND)
        else:
            self._show(State.FAILED)

    def _show(self, state: State, chip: Chip | None = None, card: ChipCard | None = None) -> None:
        self._state = state
        self._chip = chip if state is State.SHOWN else None
        shown = state is State.SHOWN and card is not None
        self._fill(card if shown else ChipCard(title=""))
        self.card.setVisible(shown)
        self.message_label.setText(
            {
                State.NO_DATA: _NO_DATA,
                State.NO_CHIP: _NO_CHIP,
                State.LOOKING: _LOOKING,
                State.NOT_FOUND: _NOT_FOUND,
                State.FAILED: _FAILED,
            }.get(state, "")
        )
        self.message_label.setVisible(not shown)
        self.clear_button.setEnabled(self._wanted is not None)

    def _fill(self, card: ChipCard) -> None:
        self.title_label.setText(card.title)
        self.maker_label.setText(card.maker)
        self.maker_label.setVisible(bool(card.maker))
        values = (card.package, card.functions, card.markings)
        for (caption, label), value in zip(self._fact_rows, values, strict=True):
            label.setText(value)
            caption.setVisible(bool(value))
            label.setVisible(bool(value))
        self.description_label.setText(card.description)
        self.description_label.setVisible(bool(card.description))
        self.disputed_label.setText(card.disputed)
        self.disputed_label.setVisible(bool(card.disputed))
