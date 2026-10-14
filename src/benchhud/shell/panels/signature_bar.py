"""The power-on signature controls on the Meter panel (#236).

Requests only, like the supply controls: the main window owns the recorder,
the job and the store.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class SignatureBar(QGroupBox):
    record_requested = Signal()
    mark_good_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Power-on signature", parent)
        self.status = QLabel(
            "Recorded automatically on Output ON. Record starts one now, for a board "
            "you power yourself."
        )
        self.status.setObjectName("SignatureStatus")
        self.status.setWordWrap(True)
        self.record_button = QPushButton("Record")
        self.record_button.setToolTip("Record the current draw for the next few seconds")
        self.record_button.clicked.connect(self.record_requested)
        self.good_button = QPushButton("Known-good")
        self.good_button.setToolTip("Use the last recording as the reference for this board number")
        self.good_button.clicked.connect(self.mark_good_requested)
        self.good_button.setEnabled(False)

        buttons = QHBoxLayout()
        buttons.addWidget(self.record_button, stretch=1)
        buttons.addWidget(self.good_button, stretch=1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

    def show_status(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet("color: #ff7a7a;" if error else "")
