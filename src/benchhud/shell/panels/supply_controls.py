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
* **When the instrument stops answering** it says so, and only Output OFF is
  left enabled: a setpoint or an Output ON sent blind can't be read back.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from benchhud.shell.qt_util import set_tone

#: DMM modes offered, as (label, the meter's mode token) (#283).
DMM_MODES = (
    ("DC volts", "VOLT:DC"),
    ("AC volts", "VOLT:AC"),
    ("DC amps", "CURR:DC"),
    ("AC amps", "CURR:AC"),
    ("Resistance", "RES"),
    ("Continuity", "CONT"),
    ("Diode", "DIOD"),
    ("Capacitance", "CAP"),
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
    dmm_mode_requested = Signal(str)

    def __init__(
        self, confirm: ConfirmOutputOn = confirm_output_on, parent: QWidget | None = None
    ) -> None:
        super().__init__("Supply", parent)
        self._confirm = confirm
        #: A failed command's line, put back once the instrument answers again.
        self._kept_failure: str | None = None

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
        self.dmm_mode = QComboBox()
        for label, token in DMM_MODES:
            self.dmm_mode.addItem(label, token)
        self.dmm_mode.activated.connect(
            lambda _i: self.dmm_mode_requested.emit(self.dmm_mode.currentData())
        )

        self.status = QLabel()
        self.status.setObjectName("SupplyStatus")
        self.status.setWordWrap(True)

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
        grid.addWidget(QLabel("DMM"), 3, 0)
        grid.addWidget(self.dmm_mode, 3, 1, 1, 2)
        grid.addWidget(self.status, 4, 0, 1, 3)
        self.set_available(connected=False, control=False, max_voltage=5.5, max_current=1.0)

    def set_available(
        self,
        *,
        connected: bool,
        control: bool,
        max_voltage: float,
        max_current: float,
        answering: bool = True,
    ) -> None:
        """Enable what may be used now, and cap the spin boxes at the ceilings.

        ``answering`` is False while a connected instrument's polls are failing
        (#527): Output OFF stays, everything that would energize or move a rail
        waits until a poll lands.
        """
        self.voltage.setMaximum(max_voltage)
        self.current.setMaximum(max_current)
        writes = connected and control and answering
        for widget in (
            self.voltage,
            self.set_voltage_button,
            self.current,
            self.set_current_button,
            self.on_button,
            self.dmm_mode,
        ):
            widget.setEnabled(writes)
        self.off_button.setEnabled(connected)
        idle = bool(self.status.property("idle"))
        if not connected:
            self._kept_failure = None
            self._show_idle("No instrument connected.")
        elif not answering:
            # A command that failed as the instrument went quiet is still not
            # done when it comes back, so its line is kept for then (#527).
            if not idle and self.status.property("tone") == "error":
                self._kept_failure = self.status.text()
            self._show_idle("The instrument isn't answering. Output OFF is still sent.", error=True)
        elif idle and self._kept_failure is not None:
            self.show_status(self._kept_failure, error=True)
        elif not self.status.text() or idle:
            # A result on screen is left alone, read-only or not: an Output OFF
            # that failed is the one line nobody should lose.
            self._show_idle(
                f"Ready. Ceilings: {max_voltage:g} V, {max_current:g} A."
                if control
                else "Read-only: instrument control is off ([instruments] control). "
                "Output OFF still works."
            )

    def _show_idle(self, text: str, *, error: bool = False) -> None:
        """Say what's available now. The next refresh may replace this line (#524).

        A command's result goes through :meth:`show_status` and isn't idle, so a
        refresh while the instrument stays usable leaves it on screen.
        """
        self._set_status(text, error=error, idle=True)

    def show_status(self, text: str, *, error: bool = False) -> None:
        """Show a command's progress or result. It replaces any failure being kept."""
        self._kept_failure = None
        self._set_status(text, error=error, idle=False)

    def _set_status(self, text: str, *, error: bool, idle: bool) -> None:
        self.status.setText(text)
        self.status.setProperty("idle", idle)
        set_tone(self.status, "error" if error else None)

    def _ask_output_on(self) -> None:
        if self._confirm(self, self.voltage.value(), self.current.value()):
            self.output_on_requested.emit()
        else:
            self.show_status("Output left off.")
