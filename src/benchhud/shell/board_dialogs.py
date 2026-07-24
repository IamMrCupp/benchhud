"""Board-docs dialogs: search the library, and the "boards to source" worklist.

Both are plain read-only views over :mod:`benchhud.boards`. Neither shows any
customer field — the worklist is deliberately ref/device/board only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.boards.library import BoardLibrary
from benchhud.boards.worklist import BoardNeed

OpenBoard = Callable[[Path], bool]


class BoardSearchDialog(QDialog):
    """ "Do I have this board?" — live substring search over the library."""

    def __init__(
        self,
        library: BoardLibrary,
        open_board: OpenBoard | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find board")
        self.resize(620, 420)
        self._library = library
        self._open_board = open_board

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Board id, e.g. 820-00239 — blank lists everything")
        self.query_edit.textChanged.connect(self.refresh)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Board", "Format", "File"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected())

        self.status_label = QLabel()
        self.open_button = QPushButton("Open in OpenBoardView")
        self.open_button.clicked.connect(self.open_selected)
        self.open_button.setEnabled(False)
        self.tree.currentItemChanged.connect(
            lambda *_: self.open_button.setEnabled(self.selected_path() is not None)
        )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.addButton(self.open_button, QDialogButtonBox.ButtonRole.ActionRole)

        layout = QVBoxLayout(self)
        layout.addWidget(self.query_edit)
        layout.addWidget(self.tree)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)

        self.refresh()

    def refresh(self) -> None:
        """Re-run the search and repopulate the results."""
        self.tree.clear()
        if not self._library.configured:
            self.status_label.setText("No board library configured (Preferences → Boards).")
            return
        results = self._library.search(self.query_edit.text())
        for board in results:
            item = QTreeWidgetItem([board.board_id, board.format, str(board.path)])
            item.setData(0, Qt.ItemDataRole.UserRole, board.path)
            self.tree.addTopLevelItem(item)
        for column in range(3):
            self.tree.resizeColumnToContents(column)
        plural = "board file" if len(results) == 1 else "board files"
        self.status_label.setText(f"{len(results)} {plural}.")

    def selected_path(self) -> Path | None:
        item = self.tree.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)

    def open_selected(self) -> bool:
        """Open the highlighted board file. False when there's nothing to open."""
        path = self.selected_path()
        if path is None or self._open_board is None:
            return False
        return self._open_board(path)


class BoardsToSourceDialog(QDialog):
    """Boards that open tickets need but the library doesn't have."""

    def __init__(self, needs: list[BoardNeed], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Boards to source")
        self.resize(620, 400)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Board", "Tickets", "Devices"])
        self.tree.setRootIsDecorated(False)
        for need in needs:
            item = QTreeWidgetItem([need.board_id, ", ".join(need.refs), ", ".join(need.devices)])
            self.tree.addTopLevelItem(item)
        for column in range(3):
            self.tree.resizeColumnToContents(column)

        summary = QLabel(
            f"{len(needs)} board(s) to find and download."
            if needs
            else "Nothing to source — every open ticket's board is in the library."
        )
        summary.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)
