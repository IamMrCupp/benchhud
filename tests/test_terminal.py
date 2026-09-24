"""The in-app terminal (#194): pty, host, the on-air rules, the server, the shell wiring.

The pty tests run a real ``/bin/sh`` in a real pseudo-terminal (macOS and Linux,
so CI too). Everything above it runs against :class:`FakeSession`, so the rules
about what reaches the stream are checked without timing races.
"""

from __future__ import annotations

import http.client
import json
import sys
import time
from typing import ClassVar

import pytest
from PySide6.QtWidgets import QWidget

from benchhud.browser_sources import BrowserSourceServer, TopicHub
from benchhud.config import Config
from benchhud.jobs.store import JobStore
from benchhud.shell.main_window import MainWindow
from benchhud.shell.panels.terminal import TerminalPanel
from benchhud.shell.preferences import PreferencesDialog
from benchhud.terminal import TerminalHost, login_shell
from benchhud.terminal.events import terminal_events

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty only")


class FakeSession:
    """Stands in for :class:`PtySession`: output and exit are driven by the test."""

    instances: ClassVar[list[FakeSession]] = []

    def __init__(self, argv, *, on_output, on_exit, cols=100, rows=30, **_kw) -> None:
        self.argv = list(argv)
        self.on_output, self.on_exit = on_output, on_exit
        self.size = (cols, rows)
        self.written: list[str] = []
        self.running = False
        FakeSession.instances.append(self)

    def start(self) -> None:
        self.running = True

    def emit(self, text: str) -> None:
        self.on_output(text)

    def finish(self, code: int = 0) -> None:
        self.running = False
        self.on_exit(code)

    def write(self, text: str) -> None:
        self.written.append(text)

    def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def close(self) -> None:
        self.running = False


@pytest.fixture(autouse=True)
def _reset_fakes():
    FakeSession.instances.clear()


def _host(**kw) -> TerminalHost:
    host = TerminalHost(["sh"], share_token="SHARE", session_factory=FakeSession, **kw)
    host.start()
    return host


def _session() -> FakeSession:
    return FakeSession.instances[-1]


# --- a real pty ----------------------------------------------------------------


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.02)


def test_a_real_shell_runs_reads_input_and_reports_its_size() -> None:
    host = TerminalHost(
        ["/bin/sh", "-c", "printf 'h\\303\\251llo\\n'; stty size; read x; echo got:$x; exit 3"],
        share_token="s",
    )
    host.resize(80, 24)
    host.start()
    _wait_for(lambda: "24 80" in host.read(0)[1])
    host.write("abc\r")
    _wait_for(lambda: host.state().exit_code is not None)
    text = host.read(0)[1]
    assert "héllo" in text  # UTF-8 decoded whole
    assert "got:abc" in text
    assert host.state().exit_code == 3
    host.close()


def test_the_shell_does_not_inherit_the_apps_python(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONHOME", "/app/bundle/python")
    host = TerminalHost(["/bin/sh", "-c", 'echo "home=[$PYTHONHOME] term=$TERM"'], share_token="s")
    host.start()
    _wait_for(lambda: host.state().exit_code is not None)
    assert "home=[] term=xterm-256color" in host.read(0)[1]
    host.close()


def test_close_ends_a_shell_that_would_run_forever() -> None:
    host = TerminalHost(["/bin/sh", "-c", "sleep 60"], share_token="s")
    host.start()
    started = time.monotonic()
    host.close()
    assert not host.running
    assert time.monotonic() - started < 5


def test_a_missing_shell_is_an_error_not_a_crash() -> None:
    host = TerminalHost(["/no/such/shell"], share_token="s")
    host.start()
    state = host.state()
    assert not state.running
    assert "/no/such/shell" in state.error


def test_login_shell_prefers_config_then_the_environment(monkeypatch) -> None:
    assert login_shell("/bin/zsh") == ["/bin/zsh", "-l"]
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert login_shell("") == ["/bin/bash", "-l"]


# --- the host --------------------------------------------------------------------


def test_scrollback_is_bounded_and_positions_are_absolute() -> None:
    host = _host(scrollback=1024)
    _session().emit("a" * 1000)
    _session().emit("b" * 100)
    pos, text, skipped = host.read(0)
    assert pos == 1100
    assert len(text) == 1024 and text.endswith("b" * 100)
    assert skipped  # the first 76 chars scrolled out


def test_a_restart_clears_the_screen_and_ignores_the_old_shell() -> None:
    host = _host()
    old = _session()
    old.emit("before")
    session_no = host.state().session
    host.restart()
    old.emit("late output from the old shell")
    _session().emit("after")
    assert host.state().session == session_no + 1
    assert host.read(0)[1] == "after"


def test_only_the_in_app_token_may_write() -> None:
    host = _host()
    assert host.token_mode(host.write_token) == "write"
    assert host.token_mode("SHARE") == "share"
    assert host.token_mode("") is None
    assert host.token_mode("guess") is None
    assert host.write_token != TerminalHost(["sh"], share_token="x").write_token  # per run


# --- what reaches the stream ---------------------------------------------------------


def _drain(gen, until=lambda e: False, limit: int = 50) -> list[dict]:
    """Pull events until one matches ``until`` or the stream goes quiet (heartbeat)."""
    out = []
    for _ in range(limit):
        event = next(gen)
        if event is None:
            break
        out.append(event)
        if until(event):
            break
    return out


def _stream(host: TerminalHost, mode: str):
    return terminal_events(host, mode, stopping=lambda: False, heartbeat=0.01)


def _text(events: list[dict]) -> str:
    return "".join(e["d"] for e in events if e["t"] == "data")


def test_the_in_app_view_gets_everything() -> None:
    host = _host()
    _session().emit("prompt$ ")
    events = _drain(_stream(host, "write"))
    assert events[0] == {"t": "hello", "mode": "write"}
    assert {"t": "size", "cols": 100, "rows": 30} in events
    assert _text(events) == "prompt$ "


def test_the_stream_shows_nothing_until_sharing_is_on() -> None:
    host = _host()
    _session().emit("secret typed before sharing")
    gen = _stream(host, "share")
    events = _drain(gen)
    assert {"t": "off"} in events
    assert _text(events) == ""
    host.set_sharing(True)
    _session().emit("on air")
    events = _drain(gen, until=lambda e: e["t"] == "data")
    assert {"t": "live"} in events
    assert _text(events) == "on air"  # never the pre-sharing output


def test_nothing_typed_while_paused_ever_reaches_the_stream() -> None:
    host = _host()
    host.set_sharing(True)
    gen = _stream(host, "share")
    _drain(gen)
    host.set_paused(True)
    _session().emit("export API_KEY=hunter2")
    events = _drain(gen)
    assert {"t": "paused"} in events
    assert "hunter2" not in _text(events)
    host.set_paused(False)
    _session().emit("ls")
    events = _drain(gen, until=lambda e: e["t"] == "data")
    assert "hunter2" not in _text(events)
    assert _text(events) == "ls"
    # A fresh connection (OBS reloading the source) doesn't get it either.
    assert "hunter2" not in _text(_drain(_stream(host, "share")))
    # The in-app view still has the full scrollback.
    assert "hunter2" in _text(_drain(_stream(host, "write")))


def test_the_stream_clears_when_it_leaves_live() -> None:
    host = _host()
    host.set_sharing(True)
    gen = _stream(host, "share")
    _drain(gen)
    host.set_sharing(False)
    events = _drain(gen)
    assert events[:2] == [{"t": "reset"}, {"t": "off"}]


def test_exit_and_errors_are_reported() -> None:
    host = _host()
    _session().finish(2)
    assert {"t": "exit", "code": 2} in _drain(_stream(host, "write"))
    bad = TerminalHost(["/no/such/shell"], share_token="s")
    bad.start()
    errors = [e for e in _drain(_stream(bad, "write")) if e["t"] == "error"]
    assert errors and "/no/such/shell" in errors[0]["message"]


# --- over HTTP -------------------------------------------------------------------------


@pytest.fixture
def served():
    host = _host()
    server = BrowserSourceServer(TopicHub(), 0, panels=False, terminal=host)
    server.start()
    yield host, server
    server.stop()
    host.close()


def _request(server, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_the_page_needs_a_token(served) -> None:
    host, server = served
    assert _request(server, "GET", "/terminal")[0] == 403
    assert _request(server, "GET", "/terminal?token=nope")[0] == 403
    status, page = _request(server, "GET", f"/terminal?token={host.write_token}")
    assert status == 200 and b"xterm.js" in page
    assert _request(server, "GET", "/terminal?token=SHARE")[0] == 200


def test_typing_needs_the_in_app_token(served) -> None:
    host, server = served
    ok = _request(server, "POST", f"/terminal/input?token={host.write_token}", body="ls\r")
    assert ok[0] == 204
    assert _request(server, "POST", "/terminal/input?token=SHARE", body="rm -rf ~\r")[0] == 403
    assert _request(server, "POST", "/terminal/input", body="x")[0] == 403
    assert _session().written == ["ls\r"]


def test_a_post_from_another_origin_is_refused(served) -> None:
    """A web page elsewhere can't drive the shell, even if it guessed the token."""
    host, server = served
    path = f"/terminal/input?token={host.write_token}"
    status = _request(server, "POST", path, body="x", headers={"Origin": "https://evil.example"})
    assert status[0] == 403
    assert _session().written == []


def test_resize_reaches_the_pty(served) -> None:
    host, server = served
    body = json.dumps({"cols": 132, "rows": 43})
    assert _request(server, "POST", f"/terminal/resize?token={host.write_token}", body)[0] == 204
    assert _session().size == (132, 43)
    bad = _request(server, "POST", f"/terminal/resize?token={host.write_token}", "{}")
    assert bad[0] == 400


def test_panels_are_off_when_only_the_terminal_runs(served) -> None:
    _, server = served
    assert _request(server, "GET", "/panel/meter")[0] == 404
    assert _request(server, "GET", "/")[0] == 404


def test_vendored_xterm_is_served_locally(served) -> None:
    _, server = served
    for name in ("xterm.js", "xterm.css", "addon-fit.js"):
        status, body = _request(server, "GET", f"/static/{name}")
        assert status == 200 and body


def test_events_stream_over_http(served) -> None:
    host, server = served
    _session().emit("hello from the shell")
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", f"/terminal/events?token={host.write_token}")
    response = conn.getresponse()
    seen = []
    while not any(e.get("t") == "data" for e in seen):
        line = response.fp.readline().decode()
        if line.startswith("data: "):
            seen.append(json.loads(line[6:]))
    assert seen[0] == {"t": "hello", "mode": "write"}
    assert _text(seen) == "hello from the shell"
    conn.close()


# --- the panel and the window ---------------------------------------------------------------


class FakeView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.urls: list[str] = []

    def setUrl(self, url) -> None:
        self.urls.append(url.toString())


def test_panel_loads_a_view_only_when_shown(qtbot) -> None:
    panel = TerminalPanel(FakeView)
    qtbot.addWidget(panel)
    panel.show_message("off")
    assert panel.view is None
    panel.show_terminal("http://127.0.0.1:1/terminal?token=a")
    panel.show_terminal("http://127.0.0.1:1/terminal?token=a")
    assert panel.view.urls == ["http://127.0.0.1:1/terminal?token=a"]  # not reloaded


def test_a_broken_webengine_costs_the_terminal_not_the_hud(qtbot) -> None:
    def broken(_parent):
        raise ImportError("libnss3.so: cannot open shared object file")

    panel = TerminalPanel(broken)
    qtbot.addWidget(panel)
    panel.show_terminal("http://127.0.0.1:1/terminal?token=a")
    assert panel.url is None
    assert "libnss3" in panel.message.text()


def test_panel_share_controls(qtbot) -> None:
    panel = TerminalPanel(FakeView)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_share_state(sharing=False, paused=False)
    assert not panel.pause_button.isVisible() and not panel.on_air.isVisible()
    panel.set_share_state(sharing=True, paused=False)
    assert panel.pause_button.isVisible() and panel.on_air.isVisible()
    panel.set_share_state(sharing=True, paused=True)
    assert not panel.on_air.isVisible()
    assert panel.pause_button.text() == "Resume stream"


@pytest.fixture
def store():
    s = JobStore(":memory:")
    yield s
    s.close()


def _window(qtbot, store, **terminal) -> MainWindow:
    config = Config()
    config.browser_sources.port = 0
    for key, value in terminal.items():
        setattr(config.terminal, key, value)
    window = MainWindow(
        config,
        job_store=store,
        terminal_view_factory=FakeView,
        terminal_session_factory=FakeSession,
    )
    qtbot.addWidget(window)
    return window


def test_off_by_default(qtbot, store) -> None:
    window = _window(qtbot, store)
    window.start_browser_sources()
    assert window.terminal is None
    assert window.browser_sources is None  # nothing listens
    assert "Preferences" in window.terminal_panel.message.text()


def test_enabling_it_serves_the_terminal_without_the_browser_sources(qtbot, store) -> None:
    window = _window(qtbot, store, enabled=True)
    window.start_browser_sources()
    try:
        server = window.browser_sources
        assert server is not None and not server.panels_enabled
        assert window.terminal_panel.url == server.terminal_url(window.terminal.write_token)
        assert window._config.terminal.share_token  # generated and kept
    finally:
        window.close()
    assert window.terminal is None


def test_preferences_ok_keeps_the_running_shell(qtbot, store) -> None:
    """Pressing OK for an unrelated change must not kill the operator's shell."""
    window = _window(qtbot, store, enabled=True)
    window.start_browser_sources()
    try:
        host = window.terminal
        window._config.graph_overlay.window_seconds = 30.0
        window.restart_browser_sources()
        assert window.terminal is host
        assert len(FakeSession.instances) == 1
        window._config.terminal.shell = "/bin/zsh"  # a real change restarts it
        window.restart_browser_sources()
        assert window.terminal is not host
        assert window.terminal.argv == ["/bin/zsh", "-l"]
    finally:
        window.close()


def test_share_and_pause_reach_the_host_and_the_panel(qtbot, store) -> None:
    window = _window(qtbot, store, enabled=True, share=True)
    window.start_browser_sources()
    try:
        assert window.terminal.state().sharing
        window.terminal_panel.pause_button.setChecked(True)
        assert window.terminal.state().paused
        assert window.terminal_panel.pause_button.text() == "Resume stream"
    finally:
        window.close()


class _NoDevices:
    def cameras(self):
        return []

    def serial_ports(self):
        return []


def test_preferences_terminal_tab_round_trips(qtbot) -> None:
    config = Config()
    dialog = PreferencesDialog(config, _NoDevices())
    qtbot.addWidget(dialog)
    dialog.terminal_enabled.setChecked(True)
    dialog.terminal_share.setChecked(True)
    dialog.terminal_shell.setText(" /bin/zsh ")
    link = dialog.terminal_share_url.text()
    dialog.regenerate_share_token()
    assert dialog.terminal_share_url.text() != link
    dialog.apply_to_config()
    assert config.terminal.enabled and config.terminal.share
    assert config.terminal.shell == "/bin/zsh"
    assert config.terminal.share_token in dialog.terminal_share_url.text()


def test_terminal_config_round_trips() -> None:
    config = Config.from_dict({"terminal": {"enabled": True, "share": True, "share_token": "t"}})
    assert config.terminal.enabled and config.terminal.share
    assert config.terminal.share_token == "t"
    assert not Config().terminal.enabled and not Config().terminal.share
