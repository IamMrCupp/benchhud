"""Application entry point — launches the HUD shell.

``--version`` deliberately avoids importing Qt so it works on a headless box
with no display; the GUI imports happen only when we actually open the window.
"""

from __future__ import annotations

import sys

from benchhud import __version__

#: First arguments that are a command, answered on the terminal with no window.
COMMANDS = frozenset({"-V", "--version", "udev-rules", "config", "job", "virtual-camera"})


def use_terminal_streams() -> None:
    """Point stdin, stdout and stderr back at the process's own file descriptors.

    The packaged macOS app sends Python's output to the system log. That suits
    a windowed app and breaks a command: ``benchhud config path`` has to put
    the path on stdout, where bench-setup reads it. A stream that already is
    its descriptor is left alone.
    """
    for name, fd, mode in (("stdin", 0, "r"), ("stdout", 1, "w"), ("stderr", 2, "w")):
        stream = getattr(sys, name)
        try:
            if stream is not None and stream.fileno() == fd:
                continue
        except (AttributeError, OSError, ValueError):
            pass
        try:
            extra = {"buffering": 1} if mode == "w" else {}
            setattr(sys, name, open(fd, mode, encoding="utf-8", closefd=False, **extra))  # noqa: SIM115
        except OSError:
            pass  # no such descriptor: keep what's there


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point for ``benchhud``.

    Returns a process exit code (the Qt event-loop result when the shell runs).
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in COMMANDS:
        use_terminal_streams()
    if argv and argv[0] in {"-V", "--version"}:
        print(f"benchhud {__version__}")
        return 0
    if argv and argv[0] == "udev-rules":
        # Fixed /dev/cam-* names for OBS (#325); Qt-free, like --version.
        from benchhud.devices.camnames import udev_rules_main

        return udev_rules_main()
    if argv and argv[0] == "config":
        # Scripted config changes (bench-setup). Display-free, like --version.
        from benchhud.cli_config import main as config_main

        return config_main(argv[1:])
    if argv and argv[0] == "job":
        # Create or select a job on the running app (#433). Qt-free too.
        from benchhud.cli_job import main as job_main

        return job_main(argv[1:])
    if argv and argv[0] == "virtual-camera":
        # Register the Windows virtual camera (#124). Display-free as well.
        from benchhud.output.softcam_register import main as virtual_camera_main

        return virtual_camera_main(argv[1:])

    # Import Qt + shell lazily so ``--version`` stays display-free.
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    from benchhud import config as config_module
    from benchhud.logging_setup import configure_logging
    from benchhud.shell import config_problem
    from benchhud.shell.main_window import MainWindow

    # A config that can't be read doesn't stop the app: it comes up on
    # defaults, says so, and saves nothing over the file (#475).
    config, problem = config_problem.load_or_defaults(config_module.load)
    configure_logging(config.shell.logging.level)
    if problem is not None:
        import logging

        logging.getLogger(__name__).error("%s %s", config_problem.HEADLINE, problem)

    from benchhud import cvthreads

    cvthreads.apply(config.performance.opencv_threads)

    from benchhud.shell.icon import app_icon
    from benchhud.shell.theme import apply_theme

    # QtWebEngine (the terminal panel) needs this set before the QApplication
    # exists, even though the web view itself is only created on demand.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("benchhud")
    app.setApplicationDisplayName("benchhud")
    app.setWindowIcon(app_icon())
    from benchhud.themes import load_themes, themes_dir

    themes_folder = themes_dir(config_module.default_config_path())
    apply_theme(app, config.shell.appearance, load_themes(themes_folder)[0])

    from benchhud.browser_sources.control import default_control_path

    window = MainWindow(
        config,
        on_persist=config_problem.saver(config_module.save, problem),
        control_file=default_control_path(),
        themes_folder=themes_folder,
    )
    window.show()
    if problem is not None:
        config_problem.show(window, problem)
    window.start_preview()
    window.start_thermal()  # no-op unless a thermal camera is chosen
    window.start_telemetry()  # no-op unless [panels.telemetry] enabled and present
    window.start_output()  # no-op unless [output] enabled
    window.start_browser_sources()  # no-op unless [browser_sources] or [terminal] enabled
    window.start_obs()  # no-op unless [obs] enabled

    from benchhud.shell import quit_signals

    # A signal closes the window the normal way: output off, then save (#291).
    quit_signals.install(window.close, app)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
