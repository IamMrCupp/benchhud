"""Terminal panel: the in-app view of the shell (#194).

The terminal itself is an xterm.js page served by the local server; this panel
hosts it in a Qt web view, created only when the terminal is on, so QtWebEngine
(Chromium) never loads for anyone who doesn't use it. The view factory is
injectable, so tests never start Chromium.

The toolbar carries the stream controls, where the operator's hands already
are: an **ON STREAM** marker while the read-only view is live, and **Pause
stream**, for before pasting something that shouldn't go on air.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from benchhud.shell.panel import Panel
from benchhud.shell.qt_util import fit_combo_popup

#: Common serial speeds; the combo is editable for anything else.
BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
#: What Enter sends to a raw serial device.
LINE_ENDING_CHOICES = (
    ("Enter sends CR LF", "crlf"),
    ("Enter sends CR", "cr"),
    ("Enter sends LF", "lf"),
)

#: Builds the widget that shows the page. It must have ``setUrl(QUrl)``.
ViewFactory = Callable[[QWidget], QWidget]

_BACKGROUND = "#10141c"  # the page's own background, so loading doesn't flash white


def web_view(parent: QWidget) -> QWidget:
    """A QtWebEngine view. Imported here so Chromium loads only when used."""
    from PySide6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView(parent)
    view.page().setBackgroundColor(QColor(_BACKGROUND))
    return view


class TerminalPanel(Panel):
    panel_id = "terminal"
    panel_title = "Terminal"

    restart_requested = Signal()
    pause_toggled = Signal(bool)
    #: The operator picked something else to attach to (#174):
    #: (target, baud, line ending, local echo).
    target_changed = Signal(str, int, str, bool)

    def __init__(self, view_factory: ViewFactory | None = None, parent=None) -> None:
        super().__init__(parent)
        self._view_factory = view_factory or web_view
        self.view: QWidget | None = None
        self._url: str | None = None

        self.on_air = QLabel("● ON STREAM")
        self.on_air.setObjectName("TerminalOnAir")
        self.on_air.setStyleSheet("color: #ff5a5a; font-weight: bold;")
        self.on_air.setToolTip("The read-only terminal view is live in OBS")
        self.pause_button = QPushButton("Pause stream")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip(
            "Hide the terminal from the stream. It resumes from that moment; "
            "nothing typed while paused is ever sent."
        )
        self.pause_button.toggled.connect(self.pause_toggled)
        self.restart_button = QPushButton("Restart shell")
        self.restart_button.clicked.connect(self.restart_requested)

        bar = QHBoxLayout()
        bar.addWidget(self.on_air)
        bar.addStretch(1)
        bar.addWidget(self.pause_button)
        bar.addWidget(self.restart_button)

        # What the terminal is attached to: the shell, the OWON's query console,
        # or a raw serial port with its speed and line settings.
        self.target_combo = QComboBox()
        self.target_combo.setToolTip("Attach the terminal to a shell or a serial port")
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        for rate in BAUD_RATES:
            self.baud_combo.addItem(str(rate), rate)
        self.ending_combo = QComboBox()
        for label, value in LINE_ENDING_CHOICES:
            self.ending_combo.addItem(label, value)
        self.echo_check = QCheckBox("Local echo")
        self.echo_check.setToolTip("Show what you type, for devices that don't echo it")
        self._serial_widgets = (self.baud_combo, self.ending_combo, self.echo_check)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Connect to"))
        target_row.addWidget(self.target_combo, stretch=1)
        for widget in self._serial_widgets:
            target_row.addWidget(widget)
        self.target_combo.activated.connect(self._emit_target)
        self.baud_combo.activated.connect(self._emit_target)
        self.baud_combo.lineEdit().editingFinished.connect(self._emit_target)
        self.ending_combo.activated.connect(self._emit_target)
        self.echo_check.clicked.connect(self._emit_target)

        self.message = QLabel()
        self.message.setObjectName("TerminalMessage")
        self.message.setWordWrap(True)
        self.message.setMargin(12)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.message)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addLayout(target_row)
        layout.addWidget(self._stack, stretch=1)
        self.set_share_state(sharing=False, paused=False)
        self.show_message("")

    # --- what it's attached to (#174) ---------------------------------------

    @property
    def target(self) -> str:
        return self.target_combo.currentData() or "shell"

    @property
    def baud(self) -> int:
        try:
            return int(self.baud_combo.currentText())
        except ValueError:
            return 115200

    def set_targets(
        self,
        targets: list[tuple[str, str]],
        current: str,
        *,
        baud: int = 115200,
        line_ending: str = "crlf",
        local_echo: bool = False,
    ) -> None:
        """Fill the picker with ``(label, target)`` pairs and select ``current``.

        Repopulating never emits :attr:`target_changed`: refreshing the list
        mustn't switch what the operator is connected to.
        """
        widgets = (self.target_combo, self.baud_combo, self.ending_combo, self.echo_check)
        for widget in widgets:
            widget.blockSignals(True)
        self.target_combo.clear()
        for label, value in targets:
            self.target_combo.addItem(label, value)
        if self.target_combo.findData(current) < 0:
            self.target_combo.addItem(f"{current} (not connected)", current)
        self.target_combo.setCurrentIndex(self.target_combo.findData(current))
        fit_combo_popup(self.target_combo)
        self.baud_combo.setCurrentText(str(baud))
        ending = self.ending_combo.findData(line_ending)
        self.ending_combo.setCurrentIndex(ending if ending >= 0 else 0)
        self.echo_check.setChecked(local_echo)
        for widget in widgets:
            widget.blockSignals(False)
        self._show_serial_options()

    def _is_raw_serial(self) -> bool:
        return self.target not in ("shell", "owon")

    def _show_serial_options(self) -> None:
        raw = self._is_raw_serial()
        for widget in self._serial_widgets:
            widget.setVisible(raw)
        self.restart_button.setText("Restart shell" if self.target == "shell" else "Reconnect")

    def _emit_target(self, *_args) -> None:
        self._show_serial_options()
        self.target_changed.emit(
            self.target,
            self.baud,
            self.ending_combo.currentData() or "crlf",
            self.echo_check.isChecked(),
        )

    def focus_terminal(self) -> None:
        """Give the terminal keyboard focus (a tool was just put at its prompt)."""
        if self.view is not None:
            self.view.setFocus()

    @property
    def url(self) -> str | None:
        """The page the view is showing, or None when a message is up."""
        return self._url if self._stack.currentWidget() is self.view else None

    def show_message(self, text: str) -> None:
        """Show ``text`` instead of the terminal (off, unsupported, port taken)."""
        self.message.setText(text)
        self._stack.setCurrentWidget(self.message)
        self.restart_button.setEnabled(False)

    def show_terminal(self, url: str) -> None:
        """Show the terminal page at ``url`` (loads only if it changed)."""
        if self.view is None:
            try:
                self.view = self._view_factory(self)
            except (ImportError, OSError) as exc:
                # A broken QtWebEngine (a missing system library on Linux) must
                # cost the terminal, not the whole HUD.
                self.show_message(f"The terminal needs QtWebEngine, which didn't load: {exc}")
                return
            self._stack.addWidget(self.view)
        if url != self._url:
            self._url = url
            self.view.setUrl(QUrl(url))
        self._stack.setCurrentWidget(self.view)
        self.restart_button.setEnabled(True)

    def set_share_state(self, *, sharing: bool, paused: bool) -> None:
        """Reflect the stream view: the Pause button only means something while sharing."""
        self.pause_button.setVisible(sharing)
        self.pause_button.blockSignals(True)
        self.pause_button.setChecked(paused)
        self.pause_button.blockSignals(False)
        self.pause_button.setText("Resume stream" if paused else "Pause stream")
        self.on_air.setVisible(sharing and not paused)
