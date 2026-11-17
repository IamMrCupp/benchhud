"""benchhud's commands answer on the terminal, also from inside the Mac app."""

from __future__ import annotations

import subprocess
import sys

from benchhud import app


def test_a_stream_that_is_not_the_terminal_is_replaced(monkeypatch) -> None:
    """The packaged macOS app's stdout is a system-log writer with no file descriptor."""

    class LogWriter:
        def write(self, text: str) -> int:
            return len(text)

    monkeypatch.setattr(sys, "stdout", LogWriter())
    app.use_terminal_streams()
    assert sys.stdout.fileno() == 1


def test_a_stream_that_is_the_terminal_is_left_alone(monkeypatch) -> None:
    class Real:
        def fileno(self) -> int:
            return 1

    real = Real()
    monkeypatch.setattr(sys, "stdout", real)
    app.use_terminal_streams()
    assert sys.stdout is real


def test_a_command_reaches_stdout_even_when_python_writes_elsewhere(tmp_path) -> None:
    """What the Mac app's launcher does: swap sys.stdout before running the module."""
    code = (
        "import sys, runpy\n"
        "class Log:\n"
        "    def write(self, text): return len(text)\n"
        "    def flush(self): pass\n"
        "sys.stdout = Log()\n"
        "sys.argv = ['benchhud', 'config', 'path']\n"
        "try:\n    runpy.run_module('benchhud', run_name='__main__')\n"
        "except SystemExit as e:\n    assert e.code == 0, e.code\n"
        "assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt was imported'\n"
    )
    config = tmp_path / "config.toml"
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={"BENCHHUD_CONFIG": str(config), "PATH": "/usr/bin:/bin"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(config)


def test_no_arguments_does_not_touch_the_streams(monkeypatch) -> None:
    assert "config" in app.COMMANDS and "--version" in app.COMMANDS
    assert not ({"", "-psn_0_1"} & app.COMMANDS)
