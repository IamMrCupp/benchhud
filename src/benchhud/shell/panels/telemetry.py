"""Telemetry panel — live DMM + PSU readings from the OWON SPM8104.

Render-only: it displays whatever :class:`TelemetrySnapshot` it's handed via
:meth:`update_reading`. The polling loop (and the real serial connection) wire in
separately, so the panel is testable off-hardware.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.shell.panel import Panel

_PLACEHOLDER = "—"


class TelemetryPanel(Panel):
    panel_id = "telemetry"
    panel_title = "Telemetry"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dmm = QLabel(f"DMM: {_PLACEHOLDER}")
        self._voltage = QLabel(f"PSU V: {_PLACEHOLDER}")
        self._current = QLabel(f"PSU I: {_PLACEHOLDER}")

        layout = QVBoxLayout(self)
        for label in (self._dmm, self._voltage, self._current):
            layout.addWidget(label)
        layout.addStretch(1)

    # Not named ``update`` — that's a QWidget method (schedules a repaint).
    def update_reading(self, snapshot: TelemetrySnapshot) -> None:
        """Refresh the labels from a telemetry snapshot."""
        if not snapshot.ok:
            self._dmm.setText(f"DMM: error ({snapshot.error})")
            self._voltage.setText(f"PSU V: {_PLACEHOLDER}")
            self._current.setText(f"PSU I: {_PLACEHOLDER}")
            return

        if snapshot.dmm is not None:
            dmm = snapshot.dmm
            shown = f"{dmm.value:g}" if dmm.value is not None else (dmm.status or _PLACEHOLDER)
            self._dmm.setText(f"DMM [{dmm.mode}]: {shown}")
        if snapshot.voltage is not None:
            self._voltage.setText(f"PSU V: {snapshot.voltage:.3f} V")
        if snapshot.current is not None:
            self._current.setText(f"PSU I: {snapshot.current:.3f} A")

    # Read-only accessors for tests.
    @property
    def dmm_text(self) -> str:
        return self._dmm.text()

    @property
    def voltage_text(self) -> str:
        return self._voltage.text()

    @property
    def current_text(self) -> str:
        return self._current.text()
