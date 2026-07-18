"""Application entry point — launches the HUD shell.

``--version`` deliberately avoids importing Qt so it works on a headless box
with no display; the GUI imports happen only when we actually open the window.
"""

from __future__ import annotations

import sys

from benchhud import __version__


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point for ``benchhud``.

    Returns a process exit code (the Qt event-loop result when the shell runs).
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-V", "--version"}:
        print(f"benchhud {__version__}")
        return 0

    # Import Qt lazily so ``--version`` stays display-free.
    from PySide6.QtWidgets import QApplication

    from benchhud.shell.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("benchhud")
    app.setApplicationDisplayName("benchhud")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
