"""Close the window cleanly when benchhud is told to stop (#291).

A signal (``systemctl --user stop``, ``kill``, logging out, Ctrl+C) used to end
the process without running the window's close path: the supply output stayed
as it was and nothing was saved. Here SIGTERM and SIGINT close the window the
normal way, so the output-off invariant and the save both run.

Python only runs a signal handler between bytecodes, and Qt's event loop sits
in C, so a no-op timer wakes the interpreter often enough to notice.
"""

from __future__ import annotations

import signal
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

#: How often the interpreter wakes to run a pending signal handler.
WAKE_MS = 250

QUIT_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def install(close: Callable[[], object], parent: QObject) -> QTimer:
    """Route the quit signals to ``close``, run on the event loop. Returns the wake timer."""

    def on_signal(_signum: int, _frame: object) -> None:
        # Never close from inside the handler: let the event loop do it.
        QTimer.singleShot(0, close)

    for sig in QUIT_SIGNALS:
        signal.signal(sig, on_signal)
    wake = QTimer(parent)
    wake.timeout.connect(lambda: None)
    wake.start(WAKE_MS)
    return wake
