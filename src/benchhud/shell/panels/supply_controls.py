"""Supply controls on the Meter panel (#164): setpoints, output, DMM mode.

This drives a power supply wired to someone's board, so the widget is built
around the safety rules in #164, and deliberately does no instrument I/O itself:
it emits requests, and the main window runs each one once through the
:class:`~benchhud.instruments.session.InstrumentSession`.

* **Confirm on enable.** Output ON asks first, naming the setpoints, with No as
  the default button. It's a deliberate act, not a toggle you brush past.
* **Output OFF is always one click,** and works with control off too: the
  driver exempts it, because the gate exists to stop the HUD energizing a rail,
  never to stop it de-energizing one.
* **The ceilings live in the driver.** The spin boxes stop at the configured
  limits as a convenience; the driver refuses anything over them regardless.
* **Nothing is retried.** A failed command is shown and left for a human.
* **With control off** (the default) it says so, and only Output OFF works.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

#: Asks the operator to confirm turning the output on; True means go ahead.
ConfirmOutputOn = Callable[[QWidget, float, float], bool]


def confirm_output_on(parent: QWidget, volts: float, amps: float) -> bool:
    answer = QMessageBox.question(
        parent,
        "Turn the output on?",
        f"Energize the board at {volts:.2f} V, current limited to {amps:.3f} A?\n\n"
        "Check the rail and the setpoints first.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,  # the default: Enter doesn't energize a rail
    )
    return answer == QMessageBox.StandardButton.Yes


class SupplyControls(QGroupBox):
    """Requests only: the window runs them through the instrument session."""

    set_voltage_requested = Signal(float)
    set_current_requested = Signal(float)
    output_on_requested = Signal()
    output_off_requested = Signal()

    def __init__(
        self, confirm: ConfirmOutputOn = confirm_output_on, parent: QWidget | None = None
    ) -> None:
        super().__init__("Supply", parent)
        self._confirm = confirm

        self.voltage = QDoubleSpinBox()
        self.voltage.setDecimals(2)
        self.voltage.setSingleStep(0.1)
        self.voltage.setSuffix(" V")
        self.set_voltage_button = QPushButton("Set")
        self.set_voltage_button.clicked.connect(
            lambda: self.set_voltage_requested.emit(self.voltage.value())
        )
        self.current = QDoubleSpinBox()
        self.current.setDecimals(3)
        self.current.setSingleStep(0.01)
        self.current.setSuffix(" A")
        self.set_current_button = QPushButton("Set")
        self.set_current_button.clicked.connect(
            lambda: self.set_current_requested.emit(self.current.value())
        )
        self.on_button = QPushButton("Output ON…")
        self.on_button.clicked.connect(self._ask_output_on)
        self.off_button = QPushButton("Output OFF")
        self.off_button.setToolTip("Always available: dropping a rail never needs control on")
        self.off_button.clicked.connect(self.output_off_requested)

        self.status = QLabel()
        self.status.setObjectName("SupplyStatus")
        self.status.setWordWrap(True)
        # The SPM8104 takes no DMM mode change over SCPI (#280): it's the dial.
        note = QLabel("Set the DMM mode on the meter itself.")
        note.setObjectName("SupplyNote")
        note.setWordWrap(True)

        # The output buttons get a row of their own: sharing the Set column
        # made that column as wide as "Output OFF", and the meter dock too
        # narrow to show it.
        output_row = QHBoxLayout()
        output_row.addWidget(self.on_button, stretch=1)
        output_row.addWidget(self.off_button, stretch=1)

        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("Voltage"), 0, 0)
        grid.addWidget(self.voltage, 0, 1)
        grid.addWidget(self.set_voltage_button, 0, 2)
        grid.addWidget(QLabel("Current limit"), 1, 0)
        grid.addWidget(self.current, 1, 1)
        grid.addWidget(self.set_current_button, 1, 2)
        grid.addLayout(output_row, 2, 0, 1, 3)
        grid.addWidget(self.status, 3, 0, 1, 3)
        grid.addWidget(note, 4, 0, 1, 3)
        self.set_available(connected=False, control=False, max_voltage=5.5, max_current=1.0)

    def set_available(
        self, *, connected: bool, control: bool, max_voltage: float, max_current: float
    ) -> None:
        """Enable what may be used now, and cap the spin boxes at the ceilings."""
        self.voltage.setMaximum(max_voltage)
        self.current.setMaximum(max_current)
        writes = connected and control
        for widget in (
            self.voltage,
            self.set_voltage_button,
            self.current,
            self.set_current_button,
            self.on_button,
        ):
            widget.setEnabled(writes)
        self.off_button.setEnabled(connected)
        if not connected:
            self.show_status("No instrument connected.")
        elif not control:
            self.show_status(
                "Read-only: instrument control is off ([instruments] control). "
                "Output OFF still works."
            )
        elif not self.status.text() or self.status.property("idle"):
            self.show_status(f"Ready. Ceilings: {max_voltage:g} V, {max_current:g} A.")
            self.status.setProperty("idle", True)

    def show_status(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setProperty("idle", False)
        self.status.setStyleSheet("color: #ff6b6b;" if error else "")

    def _ask_output_on(self) -> None:
        if self._confirm(self, self.voltage.value(), self.current.value()):
            self.output_on_requested.emit()
        else:
            self.show_status("Output left off.")
