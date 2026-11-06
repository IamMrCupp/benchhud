"""Engagement panel — the active bench-recon engagement, by ref.

Backed by the :class:`~benchhud.recon.source.ReconSource` seam: "Import from
engagement…" sets the active ref (benchhud stores only the ref), and the panel
shows that engagement's ref-safe projection — ref, operator-set title, target
*class* and status — read live so it tracks bench-recon.

**Off air.** The panel is tagged ``sensitive`` (:mod:`benchhud.output.onair`):
engagement identity never reaches the virtual camera, even though every field it
shows is from the ref-safe projection. The safe default is the point — a recon
surface is off air unless someone decides otherwise, and the pinout crib (a
separate panel) is where stream-safe recon content lives.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from benchhud.recon.source import Engagement, ReconSource
from benchhud.shell.engagement_picker import EngagementPickerDialog
from benchhud.shell.panel import PANEL_MARGINS, Panel

ReconSourceProvider = Callable[[], ReconSource]
#: The active engagement ref benchhud is working against, or None.
ActiveRefProvider = Callable[[], str | None]
#: Make a ref the active engagement; returns the resolved projection or None.
ActivateHook = Callable[[str], Engagement | None]

_PLACEHOLDER = "—"


class EngagementPanel(Panel):
    panel_id = "engagement"
    panel_title = "Engagement"
    #: Engagement identity is sensitive — off air by default (Track D).
    sensitive = True

    def __init__(
        self,
        source: ReconSourceProvider,
        active_ref: ActiveRefProvider | None = None,
        on_activate: ActivateHook | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._active_ref = active_ref
        self._on_activate = on_activate

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_action_row())

        form = QFormLayout()
        self.ref_label = QLabel(_PLACEHOLDER)
        self.title_label = QLabel(_PLACEHOLDER)
        self.title_label.setWordWrap(True)
        self.target_label = QLabel(_PLACEHOLDER)
        self.status_label = QLabel(_PLACEHOLDER)
        form.addRow("Ref", self.ref_label)
        form.addRow("Title", self.title_label)
        form.addRow("Target class", self.target_label)
        form.addRow("Status", self.status_label)
        layout.addLayout(form)

        self.message_label = QLabel()
        self.message_label.setObjectName("EngagementMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(False)
        layout.addWidget(self.message_label)

        off_air = QLabel("Off air — engagement identity is never streamed.")
        off_air.setObjectName("EngagementOffAir")
        off_air.setWordWrap(True)
        layout.addWidget(off_air)

        layout.addStretch(1)
        self.refresh()

    # --- construction ----------------------------------------------------

    def _build_action_row(self) -> QHBoxLayout:
        self.import_button = QPushButton("Import from engagement…")
        self.import_button.setToolTip("Pick a bench-recon engagement to work against")
        self.import_button.clicked.connect(self.import_from_engagement)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(self.import_button)
        row.addStretch(1)
        row.addWidget(self.refresh_button)
        return row

    # --- behaviour -------------------------------------------------------

    def import_from_engagement(self) -> EngagementPickerDialog | None:
        """Open the engagement list, let the operator pick one, make it active."""
        engagements = self._source().list_engagements()
        dialog = EngagementPickerDialog(engagements, parent=self)
        dialog.accepted.connect(lambda: self._activate_selected(dialog))
        dialog.open()
        return dialog

    def _activate_selected(self, dialog: EngagementPickerDialog) -> None:
        engagement = dialog.selected_engagement()
        if engagement is not None and self._on_activate is not None:
            self._on_activate(engagement.ref)
        self.refresh()

    def refresh(self) -> None:
        """Reload the active engagement's ref-safe projection from the source."""
        source = self._source()
        ref = self._active_ref() if self._active_ref is not None else None
        engagement = source.get_engagement(ref) if ref else None
        self.import_button.setVisible(source.name != "none")
        self._show(engagement, source_name=source.name, ref=ref)

    def _show(self, engagement: Engagement | None, *, source_name: str, ref: str | None) -> None:
        if engagement is not None:
            self.ref_label.setText(engagement.ref)
            self.title_label.setText(engagement.title or _PLACEHOLDER)
            self.target_label.setText(engagement.target_class or _PLACEHOLDER)
            self.status_label.setText(engagement.status or _PLACEHOLDER)
            self.message_label.setVisible(False)
            return
        for label in (self.ref_label, self.title_label, self.target_label, self.status_label):
            label.setText(_PLACEHOLDER)
        if source_name == "none":
            self._message("No recon backend configured — see Preferences.")
        elif ref:
            self._message(f"Engagement {ref} not found (or bench-recon is unreachable).")
        else:
            self._message("No engagement imported. Use “Import from engagement…”.")

    def _message(self, text: str) -> None:
        self.message_label.setText(text)
        self.message_label.setVisible(True)
