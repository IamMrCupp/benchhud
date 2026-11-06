"""Pinout / protocol reference panel — a static crib beside the engagement.

A quick on-bench reference for the wiring protocols a hardware/IoT engagement
runs into — JTAG, SWD, UART, CAN, SPI, I²C — with the signals and the gotchas
that bite (CAN termination, I²C pull-ups, UART cross-over). It also shows the
active engagement's ref-safe context so the crib sits beside the job.

**Stream-safe (not sensitive).** Everything here is generic reference material or
the ref-safe engagement projection; the sensitive target notes stay in
bench-recon and never reach benchhud, so there is nothing to keep off air. This
is the stream-safe recon panel the Track D design left room for.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from benchhud.shell.panel import PANEL_MARGINS, Panel

ActiveRefProvider = Callable[[], str | None]

#: The crib: each protocol, a one-line note, and its (signal, description) rows.
#: Static reference — no device or engagement data, so it is safe anywhere.
PROTOCOLS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "JTAG",
        "Boundary-scan / debug. 5 signals + ground; VREF sets I/O level.",
        (
            ("TCK", "Test clock"),
            ("TMS", "Test mode select"),
            ("TDI", "Test data in"),
            ("TDO", "Test data out"),
            ("TRST", "Test reset (optional)"),
            ("VREF / GND", "Reference voltage and ground"),
        ),
    ),
    (
        "SWD",
        "ARM 2-wire debug. Shares the pins with JTAG on SWJ-DP parts.",
        (
            ("SWDIO", "Bidirectional data"),
            ("SWCLK", "Clock"),
            ("SWO", "Trace out (optional, from TDO pin)"),
            ("VREF / GND", "Reference voltage and ground"),
        ),
    ),
    (
        "UART",
        "Async serial. TX↔RX cross over; match baud/parity; level-shift 3V3 vs 5V.",
        (
            ("TX", "Transmit → peer RX"),
            ("RX", "Receive ← peer TX"),
            ("GND", "Common ground (connect first)"),
            ("RTS / CTS", "Hardware flow control (optional)"),
        ),
    ),
    (
        "CAN",
        "Differential bus. 120Ω termination at both ends; a dongle may supply one.",
        (
            ("CANH", "Bus high"),
            ("CANL", "Bus low"),
            ("GND", "Common ground"),
        ),
    ),
    (
        "SPI",
        "4-wire, master-timed. Watch CPOL/CPHA mode and who drives CS.",
        (
            ("SCLK", "Clock (from master)"),
            ("MOSI", "Master out, slave in"),
            ("MISO", "Master in, slave out"),
            ("CS / SS", "Chip select (active low)"),
            ("VCC / GND", "Power and ground"),
        ),
    ),
    (
        "I²C",
        "2-wire, open-drain. Needs pull-ups (often 4.7kΩ); mind 7-bit addresses.",
        (
            ("SDA", "Data (open-drain, pulled up)"),
            ("SCL", "Clock (open-drain, pulled up)"),
            ("VCC / GND", "Power and ground"),
        ),
    ),
)


class PinoutPanel(Panel):
    panel_id = "pinout"
    panel_title = "Pinout & protocols"
    # Stream-safe: static reference + the ref-safe engagement context only.

    def __init__(
        self,
        active_ref: ActiveRefProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._active_ref = active_ref

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*PANEL_MARGINS)
        layout.addLayout(self._build_header())

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Signal", "Notes"])
        self.tree.setMinimumWidth(1)  # never force a wide dock (#230); it scrolls
        self._populate()
        layout.addWidget(self.tree)

        self.refresh()

    def _build_header(self) -> QHBoxLayout:
        self.ref_label = QLabel("—")
        # Let the ref shrink with a narrow dock rather than forcing it wide (#230).
        self.ref_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.ref_label.setMinimumWidth(1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Engagement"))
        row.addWidget(self.ref_label, stretch=1)
        return row

    def _populate(self) -> None:
        for name, note, signals in PROTOCOLS:
            parent = QTreeWidgetItem([name, note])
            for signal, description in signals:
                parent.addChild(QTreeWidgetItem([signal, description]))
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)

    def refresh(self) -> None:
        """Update the ref-safe engagement context line."""
        ref = self._active_ref() if self._active_ref is not None else None
        self.ref_label.setText(ref or "— (no engagement)")
