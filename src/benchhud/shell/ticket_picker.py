"""Ticket picker — choose an open intake ticket to import onto the bench.

Lists the open queue from a :class:`~benchhud.tickets.source.TicketSource` so the
operator imports a ticket instead of retyping it.

This is a **non-composited** dialog, so it may show the ``customer`` name for
identification — that (and every ticket field) never reaches the virtual camera.
``board_id`` is non-PII and safe anywhere.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.tickets.source import Ticket


class TicketPickerDialog(QDialog):
    """Lists open tickets; returns the selected one via :meth:`selected_ticket`."""

    def __init__(self, tickets: list[Ticket], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import from ticket")
        self.resize(600, 400)
        self._tickets = tickets

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Ref", "Device", "Board", "Customer"])
        self.tree.setRootIsDecorated(False)
        self._populate()
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())

        summary = QLabel(
            f"{len(tickets)} open ticket(s) from intake."
            if tickets
            else "No open tickets (or intake is unreachable)."
        )
        summary.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(False)
        self.tree.currentItemChanged.connect(
            lambda *_: self._ok_button.setEnabled(self.selected_ticket() is not None)
        )

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        for ticket in self._tickets:
            item = QTreeWidgetItem(
                [ticket.ref, ticket.device, ticket.board_id or "—", ticket.customer]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, ticket)
            self.tree.addTopLevelItem(item)
        for column in range(4):
            self.tree.resizeColumnToContents(column)

    def selected_ticket(self) -> Ticket | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)
