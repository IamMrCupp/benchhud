"""The terminal on Windows (#203): choosing the backend, the shell, and ConPTY.

Three layers:

* Selection and shell choice, with the platform faked. Every OS.
* :class:`ConPtySession`'s own logic against :class:`FakePty`, a stand-in for
  ``winpty.PTY``. Every OS.
* A real ``cmd.exe`` in a real pseudo-console. Windows only; these are skipped
  everywhere else, so a green Linux run says nothing about them.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from benchhud import terminal
from benchhud.terminal import TerminalHost, login_shell
from benchhud.terminal.conpty_session import ConPtySession, command_tail, env_block
from benchhud.terminal.host import default_session_factory

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs a real ConPTY")


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.02)


# --- which backend, which shell (platform faked) ---------------------------------


def test_windows_gets_the_conpty_session() -> None:
    assert default_session_factory("win32") is ConPtySession


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX pty doesn't import on Windows")
def test_everything_else_gets_the_posix_pty() -> None:
    from benchhud.terminal.pty_session import PtySession

    assert default_session_factory("linux") is PtySession
    assert default_session_factory("darwin") is PtySession


def test_the_host_picks_the_session_for_the_platform(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    host = TerminalHost(["cmd.exe"], share_token="s")
    assert host._session_factory is ConPtySession


def test_the_terminal_is_supported_here() -> None:
    assert terminal.SUPPORTED


def test_windows_runs_comspec_and_not_a_login_shell() -> None:
    env = {"COMSPEC": r"C:\Windows\System32\cmd.exe", "SHELL": "/bin/bash"}
    assert login_shell("", platform="win32", environ=env) == [r"C:\Windows\System32\cmd.exe"]


def test_windows_without_comspec_runs_powershell() -> None:
    assert login_shell("", platform="win32", environ={}) == ["powershell.exe"]


def test_a_configured_windows_shell_is_one_program_spaces_and_all() -> None:
    pwsh = r"C:\Program Files\PowerShell\7\pwsh.exe"
    assert login_shell(f" {pwsh} ", platform="win32", environ={"COMSPEC": "cmd.exe"}) == [pwsh]


def test_posix_still_runs_a_login_shell() -> None:
    assert login_shell("", platform=sys.platform, environ={"SHELL": "/bin/bash"})[1:] == (
        [] if sys.platform == "win32" else ["-l"]
    )
    assert login_shell("/bin/zsh", platform="linux", environ={}) == ["/bin/zsh", "-l"]
    assert login_shell("", platform="linux", environ={"SHELL": "/bin/bash"}) == ["/bin/bash", "-l"]


# --- ConPtySession against a fake pty ---------------------------------------------


class FakePty:
    """``winpty.PTY``'s surface, driven by the test."""

    def __init__(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)
        self.pid = None  # nothing for close() to kill
        self.spawned: dict | None = None
        self.spawn_result = True
        self.written: list[str] = []
        self.exit_status: int | None = None
        self._pending: list[str] = []
        self._lock = threading.Lock()

    # winpty.PTY -------------------------------------------------------------
    def spawn(self, appname, cmdline=None, cwd=None, env=None) -> bool:
        self.spawned = {"appname": appname, "cmdline": cmdline, "cwd": cwd, "env": env}
        return self.spawn_result

    def read(self, blocking: bool = False) -> str:
        assert blocking is False  # a blocking read can hang on a dead shell
        with self._lock:
            text, self._pending = "".join(self._pending), []
        return text

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def set_size(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def isalive(self) -> bool:
        return self.exit_status is None

    def get_exitstatus(self) -> int | None:
        return self.exit_status

    # the test's side ----------------------------------------------------------
    def emit(self, text: str) -> None:
        with self._lock:
            self._pending.append(text)

    def exit(self, code: int, last_words: str = "") -> None:
        if last_words:
            self.emit(last_words)
        self.exit_status = code


class Recorder:
    def __init__(self) -> None:
        self.output: list[str] = []
        self.exits: list[int] = []

    @property
    def text(self) -> str:
        return "".join(self.output)


def _fake_session(argv=None, **kw) -> tuple[ConPtySession, list[FakePty], Recorder]:
    ptys: list[FakePty] = []
    seen = Recorder()

    def open_pty(cols: int, rows: int) -> FakePty:
        ptys.append(FakePty(cols, rows))
        return ptys[-1]

    session = ConPtySession(
        argv or [sys.executable],
        on_output=seen.output.append,
        on_exit=seen.exits.append,
        open_pty=open_pty,
        **kw,
    )
    return session, ptys, seen


def test_it_spawns_the_resolved_program_with_a_quoted_tail_and_an_env_block(tmp_path) -> None:
    session, ptys, _seen = _fake_session(
        [sys.executable, "-c", "print('a b')"],
        cwd=str(tmp_path),
        env={"PATH": "", "A": "1", "B": "two words"},
        cols=120,
        rows=40,
    )
    session.start()
    try:
        pty = ptys[0]
        assert pty.size == (120, 40)
        assert pty.spawned == {
            "appname": sys.executable,
            "cmdline": " -c \"print('a b')\"",
            "cwd": str(tmp_path),
            "env": "PATH=\0A=1\0B=two words\0",
        }
    finally:
        session.close()


def test_no_arguments_means_no_command_tail() -> None:
    assert command_tail(["cmd.exe"]) is None
    assert command_tail(["cmd.exe", "/k", "echo hi"]) == ' /k "echo hi"'
    assert env_block({}) == ""


def test_output_arrives_and_the_exit_code_follows_the_last_words() -> None:
    session, ptys, seen = _fake_session()
    session.start()
    pty = ptys[0]
    pty.emit("C:\\> ")
    _wait_for(lambda: "C:\\> " in seen.text)
    assert session.running
    pty.exit(3, last_words="bye\r\n")
    _wait_for(lambda: seen.exits == [3])
    assert seen.text == "C:\\> bye\r\n"  # nothing lost on the way out
    _wait_for(lambda: not session.running)
    session.close()


def test_typing_and_resizing_reach_the_pty_and_stop_once_it_has_gone() -> None:
    session, ptys, seen = _fake_session()
    session.start()
    pty = ptys[0]
    session.write("dir\r")
    session.resize(132, 50)
    session.resize(0, 0)  # clamped, as on POSIX
    assert pty.written == ["dir\r"]
    assert pty.size == (2, 1)
    pty.exit(0)
    _wait_for(lambda: seen.exits == [0])
    _wait_for(lambda: not session.running)
    session.write("too late")
    session.resize(80, 24)
    assert pty.written == ["dir\r"] and pty.size == (2, 1)
    session.close()


def test_close_stops_the_reader_and_lets_go_of_the_console() -> None:
    session, _ptys, seen = _fake_session()
    session.start()
    started = time.monotonic()
    session.close()
    session.close()  # idempotent
    assert not session.running
    assert session._pty is None  # the last reference: Windows closes the console
    assert seen.exits == [0]
    assert time.monotonic() - started < 3


def test_a_read_that_fails_ends_the_session_instead_of_spinning() -> None:
    session, ptys, seen = _fake_session()
    session.start()

    def broken(blocking: bool = False) -> str:
        raise RuntimeError("Standard out reached EOF")  # WinptyError's shape

    ptys[0].exit_status = 1
    ptys[0].read = broken
    _wait_for(lambda: seen.exits == [1])
    session.close()


def test_a_missing_program_is_file_not_found() -> None:
    session, ptys, _seen = _fake_session(["no-such-shell-anywhere.exe"])
    with pytest.raises(FileNotFoundError):
        session.start()
    assert ptys == []  # no console made for nothing


def test_pywinpty_missing_or_refusing_is_an_oserror_the_host_can_report() -> None:
    def no_pywinpty(_cols, _rows):
        raise ImportError("No module named 'winpty'")

    def no_conpty(_cols, _rows):
        raise RuntimeError("ConPTY is not available on this version of Windows")

    for opener, words in ((no_pywinpty, "pywinpty"), (no_conpty, "not available")):
        host = TerminalHost(
            [sys.executable],
            share_token="s",
            session_factory=lambda argv, opener=opener, **kw: ConPtySession(
                argv, open_pty=opener, **kw
            ),
        )
        host.start()
        state = host.state()
        assert not state.running
        assert words in state.error


def test_a_process_that_does_not_start_is_an_oserror() -> None:
    session, ptys, _seen = _fake_session()

    def refuse(cols: int, rows: int) -> FakePty:
        pty = FakePty(cols, rows)
        pty.spawn_result = False
        ptys.append(pty)
        return pty

    session._open_pty = refuse
    with pytest.raises(OSError):
        session.start()
    assert not session.running


def test_the_host_drives_a_conpty_session_end_to_end() -> None:
    ptys: list[FakePty] = []

    def factory(argv, **kw):
        def open_pty(cols, rows):
            ptys.append(FakePty(cols, rows))
            return ptys[-1]

        return ConPtySession(argv, open_pty=open_pty, **kw)

    host = TerminalHost([sys.executable], share_token="s", session_factory=factory)
    host.resize(80, 24)
    host.start()
    assert ptys[0].size == (80, 24)
    host.write("ver\r")
    ptys[0].emit("Microsoft Windows\r\n")
    _wait_for(lambda: "Microsoft Windows" in host.read(0)[1])
    host.resize(100, 30)
    assert ptys[0].size == (100, 30) and ptys[0].written == ["ver\r"]
    ptys[0].exit(7)
    _wait_for(lambda: host.state().exit_code == 7)
    host.close()


# --- a real ConPTY (Windows only) ---------------------------------------------------


def _cmd() -> str:
    import os

    return os.environ.get("COMSPEC") or "cmd.exe"


@windows_only
def test_the_default_host_on_windows_is_a_real_conpty() -> None:
    host = TerminalHost(login_shell(""), share_token="s")
    assert host._session_factory is ConPtySession


@windows_only
def test_a_real_shell_runs_reads_input_and_exits_with_its_code() -> None:
    host = TerminalHost([_cmd(), "/q", "/k", "echo ready"], share_token="s")
    host.resize(80, 24)
    host.start()
    assert host.state().error is None
    _wait_for(lambda: "ready" in host.read(0)[1], timeout=20)
    host.write("echo got:%OS%\r")
    _wait_for(lambda: "got:Windows_NT" in host.read(0)[1], timeout=20)
    host.write("exit 3\r")
    _wait_for(lambda: host.state().exit_code is not None, timeout=20)
    assert host.state().exit_code == 3
    _wait_for(lambda: not host.running)
    host.close()


@windows_only
def test_a_real_shell_sees_the_size_and_a_resize() -> None:
    host = TerminalHost([_cmd(), "/q", "/k", "mode con"], share_token="s")
    host.resize(91, 27)
    host.start()
    _wait_for(lambda: "91" in host.read(0)[1], timeout=20)
    host.resize(117, 33)
    pos = host.read(0)[0]
    host.write("mode con\r")
    _wait_for(lambda: "117" in host.read(pos)[1], timeout=20)
    host.close()
    assert not host.running


@windows_only
def test_a_real_shell_does_not_inherit_the_apps_python(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", r"C:\app\bundle\python")
    host = TerminalHost(
        [_cmd(), "/q", "/c", "echo home=[%PYTHONHOME%] term=%TERM%"], share_token="s"
    )
    host.start()
    _wait_for(lambda: host.state().exit_code is not None, timeout=20)
    # cmd leaves an unset variable's name in place, so the value must be absent.
    text = host.read(0)[1]
    assert "bundle" not in text
    assert "term=xterm-256color" in text
    host.close()


@windows_only
def test_close_ends_a_real_shell_that_would_run_forever() -> None:
    host = TerminalHost([_cmd(), "/q", "/k", "echo ready"], share_token="s")
    host.start()
    _wait_for(lambda: "ready" in host.read(0)[1], timeout=20)
    pid = host._session.pid
    assert pid
    started = time.monotonic()
    host.close()
    assert not host.running
    assert time.monotonic() - started < 5
    import ctypes

    # SYNCHRONIZE access; a wait of zero says whether the process has exited.
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            assert ctypes.windll.kernel32.WaitForSingleObject(handle, 5000) == 0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)


@windows_only
def test_a_missing_shell_on_windows_is_an_error_not_a_crash() -> None:
    host = TerminalHost([r"C:\no\such\shell.exe"], share_token="s")
    host.start()
    state = host.state()
    assert not state.running
    assert "shell.exe" in state.error
