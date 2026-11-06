"""Capture / firmware-dump status panel — artifacts by engagement ref.

Shows what has been captured or dumped for the active engagement, read from
bench-recon via :meth:`~benchhud.recon.source.ReconSource.artifacts` — metadata
only (name, kind, size, status), never the contents.

**Off air.** Tagged ``sensitive`` (:mod:`benchhud.output.onair`): a dump's
filename or size can reveal target detail, so the panel renders in the dock but
never reaches the virtual camera.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.recon.source import Artifact, ReconSource
from benchhud.shell.panel import PANEL_MARGINS, Panel

ReconSourceProvider = Callable[[], ReconSource]
ActiveRefProvider = Callable[[], str | None]

_DASH = "—"


def human_size(size: int) -> str:
    """A compact byte count: ``8.0 MB``, ``512 B``. Never scientific notation."""
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class CapturePanel(Panel):
    panel_id = "captures"
    panel_title = "Captures"
    #: Artifact names/sizes can reveal target detail — off air (Track D).
    sensitive = True

    def __init__(
        self,
        source: ReconSourceProvider,
        active_ref: ActiveRefProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._active_ref = active_ref

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_header())

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Name", "Kind", "Size", "Status"])
        self.tree.setRootIsDecorated(False)
        self.tree.setMinimumWidth(1)  # never force a wide dock (#230); it scrolls
        layout.addWidget(self.tree)

        self.message_label = QLabel()
        self.message_label.setObjectName("CaptureMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(False)
        layout.addWidget(self.message_label)

        off_air = QLabel("Off air — capture names can reveal the target.")
        off_air.setObjectName("CaptureOffAir")
        off_air.setWordWrap(True)
        layout.addWidget(off_air)

        self.refresh()

    def _build_header(self) -> QHBoxLayout:
        self.ref_label = QLabel(_DASH)
        self.ref_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.ref_label.setMinimumWidth(1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(QLabel("Engagement"))
        row.addWidget(self.ref_label, stretch=1)
        row.addWidget(self.refresh_button)
        return row

    def refresh(self) -> None:
        """Reload the active engagement's artifact metadata from the source."""
        source = self._source()
        ref = self._active_ref() if self._active_ref is not None else None
        artifacts = source.artifacts(ref) if ref else []

        self.ref_label.setText(ref or "— (no engagement)")
        self.tree.clear()
        for artifact in artifacts:
            self.tree.addTopLevelItem(self._row(artifact))
        for column in range(4):
            self.tree.resizeColumnToContents(column)
        self._show_message(artifacts, source_name=source.name, ref=ref)

    def _row(self, artifact: Artifact) -> QTreeWidgetItem:
        return QTreeWidgetItem(
            [
                artifact.name,
                artifact.kind or _DASH,
                human_size(artifact.size),
                artifact.status or _DASH,
            ]
        )

    def _show_message(
        self, artifacts: Sequence[Artifact], *, source_name: str, ref: str | None
    ) -> None:
        if source_name == "none":
            self._message("No recon backend configured — see Preferences.")
        elif ref is None:
            self._message("Import an engagement to see its captures.")
        elif not artifacts:
            self._message(f"No captures recorded for {ref} yet.")
        else:
            self.message_label.setVisible(False)

    def _message(self, text: str) -> None:
        self.message_label.setText(text)
        self.message_label.setVisible(True)
