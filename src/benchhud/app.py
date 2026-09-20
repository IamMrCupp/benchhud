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

    # Import Qt + shell lazily so ``--version`` stays display-free.
    from PySide6.QtWidgets import QApplication

    from benchhud import config as config_module
    from benchhud.logging_setup import configure_logging
    from benchhud.shell.main_window import MainWindow

    config = config_module.load()
    configure_logging(config.shell.logging.level)

    from benchhud.shell.icon import app_icon
    from benchhud.shell.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("benchhud")
    app.setApplicationDisplayName("benchhud")
    app.setWindowIcon(app_icon())
    apply_theme(app)

    window = MainWindow(config, on_persist=config_module.save)
    window.show()
    window.start_preview()
    window.start_telemetry()  # no-op unless [panels.telemetry] enabled and present
    window.start_output()  # no-op unless [output] enabled
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
