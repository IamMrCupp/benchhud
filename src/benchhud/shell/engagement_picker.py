"""Engagement picker — choose a bench-recon engagement to work against.

Lists the ref-safe projection from a :class:`~benchhud.recon.source.ReconSource`
so the operator imports an engagement by ref instead of retyping it. The
projection carries no sensitive detail (that stays in bench-recon); the
engagement panel it feeds is off air regardless.
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

from benchhud.recon.source import Engagement


class EngagementPickerDialog(QDialog):
    """Lists engagements; returns the selected one via :meth:`selected_engagement`."""

    def __init__(self, engagements: list[Engagement], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import from engagement")
        self.resize(600, 400)
        self._engagements = engagements

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Ref", "Title", "Target class", "Status"])
        self.tree.setRootIsDecorated(False)
        self._populate()
        self.tree.itemDoubleClicked.connect(lambda *_: self.accept())

        summary = QLabel(
            f"{len(engagements)} engagement(s) from bench-recon."
            if engagements
            else "No engagements (or bench-recon is unreachable)."
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
            lambda *_: self._ok_button.setEnabled(self.selected_engagement() is not None)
        )

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        for engagement in self._engagements:
            item = QTreeWidgetItem(
                [
                    engagement.ref,
                    engagement.title or "—",
                    engagement.target_class or "—",
                    engagement.status or "—",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, engagement)
            self.tree.addTopLevelItem(item)
        for column in range(4):
            self.tree.resizeColumnToContents(column)

    def selected_engagement(self) -> Engagement | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)
