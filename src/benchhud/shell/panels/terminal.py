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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from benchhud.shell.panel import Panel

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

        self.message = QLabel()
        self.message.setObjectName("TerminalMessage")
        self.message.setWordWrap(True)
        self.message.setMargin(12)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.message)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self._stack, stretch=1)
        self.set_share_state(sharing=False, paused=False)
        self.show_message("")

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
