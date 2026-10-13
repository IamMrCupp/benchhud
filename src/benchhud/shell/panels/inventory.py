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
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from benchhud.inventory.source import InventorySource, InventoryWriteError, Part
from benchhud.shell.panel import PANEL_MARGINS, Panel

InventorySourceProvider = Callable[[], InventorySource]
#: The selected job's external ticket ref, or None when no job / not linked.
JobRefProvider = Callable[[], str | None]
#: Called after a successful consume, e.g. to drop a note on the job timeline.
ConsumedHook = Callable[[Part, int], None]

_STATUS_COLOURS = {"out": QColor("#d9534f"), "low": QColor("#e8a33d")}


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

    def __init__(
        self,
        source: InventorySourceProvider,
        job_ref: JobRefProvider | None = None,
        on_consumed: ConsumedHook | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._job_ref = job_ref
        self._on_consumed = on_consumed

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_search_row())
        self.part_list = QListWidget()
        self.part_list.setMinimumHeight(90)
        self.part_list.currentItemChanged.connect(lambda *_: self._sync_use_row())
        layout.addWidget(self.part_list)
        layout.addLayout(self._build_use_row())
        self.message_label = QLabel()
        self.message_label.setObjectName("InventoryMessage")
        self.message_label.setWordWrap(True)
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

    # --- behaviour -------------------------------------------------------

    def refresh(self) -> None:
        """Reload the list: search results when a query is set, else low stock."""
        query = self.search_edit.text().strip()
        source = self._source()
        parts = source.search(query) if query else source.list_low_stock()
        self.part_list.clear()
        for part in parts:
            item = QListWidgetItem(_row_text(part))
            item.setData(Qt.ItemDataRole.UserRole, part)
            colour = _STATUS_COLOURS.get(part.status)
            if colour is not None:
                item.setForeground(colour)
            self.part_list.addItem(item)
        if not parts and source.name == "none":
            self._show_message("No inventory backend configured — see Preferences.", error=False)
        elif not parts and not query:
            self._show_message("Nothing below minimum — the shelf is healthy.", error=False)
        else:
            self.message_label.setVisible(False)
        self._sync_use_row()

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
