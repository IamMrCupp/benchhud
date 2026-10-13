"""Tool picker — pick installed bench tools instead of hand-typing commands.

Scans the :mod:`benchhud.launcher.catalog` for tools present on this system and
lets the operator tick the ones to add to the launcher. Tools that aren't
installed are still listed (greyed out), so the dialog doubles as a hint of what
a repair bench typically runs.

The detector is injected, so tests never depend on what's installed.
"""

from __future__ import annotations

from collections.abc import Callable

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

from benchhud.config import ToolConfig
from benchhud.launcher.catalog import DetectedTool, detect_tools

Detector = Callable[[], list[DetectedTool]]


class ToolPickerDialog(QDialog):
    """Lists catalog tools with found/not-found status; returns the ticked ones."""

    def __init__(
        self,
        detector: Detector = detect_tools,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Detect installed tools")
        self.resize(640, 460)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Tool", "Status", "Command", "Opens in"])
        self.tree.setRootIsDecorated(False)

        self._detected = detector()
        self._populate()

        found = sum(1 for d in self._detected if d.found)
        summary = QLabel(
            f"Found {found} of {len(self._detected)} known tools for this platform. "
            "Tick the ones to add to the Tools panel."
        )
        summary.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.tree)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        for detected in self._detected:
            tool = detected.tool
            item = QTreeWidgetItem(
                [
                    tool.name,
                    "installed" if detected.found else "not found",
                    detected.command or "",
                    "terminal  >_" if tool.terminal else "window",
                ]
            )
            tip = f"{tool.category} — {tool.description}"
            if tool.terminal:
                tip += "\nCommand line: opens in the Terminal panel."
                if tool.gui:
                    tip += f" GUI alternative: {', '.join(tool.gui)}."
            item.setToolTip(0, tip)
            if detected.found:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                item.setData(0, Qt.ItemDataRole.UserRole, detected)
            else:
                # Listed for discoverability, but nothing to add.
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(item)
        for column in range(4):
            self.tree.resizeColumnToContents(column)

    def selected_tools(self) -> list[ToolConfig]:
        """Ticked, installed tools as launcher entries."""
        chosen: list[ToolConfig] = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            detected = item.data(0, Qt.ItemDataRole.UserRole)
            if detected is None or item.checkState(0) != Qt.CheckState.Checked:
                continue
            chosen.append(
                ToolConfig(
                    name=detected.tool.name,
                    command=detected.command,
                    terminal=detected.tool.terminal,
                )
            )
        return chosen
