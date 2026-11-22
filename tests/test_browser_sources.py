"""OBS browser sources (#180): the hub, the payloads, the server and the shell wiring.

The server tests talk real HTTP to a real listener on an ephemeral loopback
port, including an SSE stream, so what's checked is what OBS will receive.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading

import pytest

from benchhud.browser_sources import PANELS, BrowserSourceServer, TopicHub
from benchhud.browser_sources import server as server_module
from benchhud.browser_sources.payloads import (
    JOB_FIELDS,
    graph_payload,
    job_payload,
    meter_payload,
)
from benchhud.config import Config
from benchhud.instruments.power_history import PowerSample
from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.jobs.store import JobStore
from benchhud.overlay.telemetry import MeterReadout
from benchhud.palette import palette
from benchhud.shell.main_window import MainWindow
from benchhud.shell.preferences import PreferencesDialog

# --- hub -------------------------------------------------------------------


def test_hub_versions_start_at_zero_and_count_up() -> None:
    hub = TopicHub()
    assert hub.latest("meter") == (0, None)
    assert hub.publish("meter", {"a": 1}) == 1
    assert hub.publish("meter", {"a": 2}) == 2
    assert hub.latest("meter") == (2, {"a": 2})
    assert hub.latest("graph") == (0, None)


def test_hub_wait_returns_a_newer_value_at_once() -> None:
    hub = TopicHub()
    hub.publish("meter", "x")
    assert hub.wait("meter", after=0, timeout=5.0) == (1, "x")


def test_hub_wait_times_out_unchanged() -> None:
    hub = TopicHub()
    hub.publish("meter", "x")
    assert hub.wait("meter", after=1, timeout=0.01) == (1, "x")


def test_hub_wait_wakes_on_publish_from_another_thread() -> None:
    hub = TopicHub()
    threading.Timer(0.05, hub.publish, args=("meter", "late")).start()
    assert hub.wait("meter", after=0, timeout=5.0) == (1, "late")


def test_wake_all_releases_waiters_and_keeps_the_hub_usable() -> None:
    hub = TopicHub()
    threading.Timer(0.05, hub.wake_all).start()
    assert hub.wait("meter", after=0, timeout=5.0) == (0, None)
    hub.publish("meter", "after restart")
    assert hub.wait("meter", after=0, timeout=5.0) == (1, "after restart")


# --- payloads --------------------------------------------------------------


def test_job_card_never_carries_the_customer() -> None:
    """The on-air rule: PII-out. Serial and symptoms stay off stream too."""
    store = JobStore(":memory:")
    job = store.create_job(
        "iPhone 12",
        serial="F2LXK0ABCDEF",
        customer="Jane Doe",
        symptoms="Jane says it died after a swim",
        external_ref="T-1042",
        board_id="820-02020",
    )
    card = job_payload(job)["job"]
    assert set(card) == set(JOB_FIELDS)
    text = json.dumps(card)
    for secret in ("Jane", "F2LXK0ABCDEF", "swim"):
        assert secret not in text
    assert card["device"] == "iPhone 12"
    assert card["board_id"] == "820-02020"
    assert card["external_ref"] == "T-1042"
    store.close()


def test_no_selected_job_is_an_explicit_none() -> None:
    assert job_payload(None) == {"job": None}


def test_meter_payload_carries_the_docks_strings_and_colours() -> None:
    readout = MeterReadout(voltage="5.00", current="0.150", dmm_mode="VDC", dmm_value="3.30")
    payload = meter_payload(readout)
    assert payload["readout"]["voltage"] == "5.00"
    assert payload["readout"]["dmm_mode"] == "VDC"
    assert payload["colours"]["voltage"]["line"] == palette().voltage
    assert payload["colours"]["max_current"]["line"] == palette().current
    json.dumps(payload)  # must be JSON-ready as is


def test_graph_payload_is_empty_but_labelled_before_readings() -> None:
    payload = graph_payload([], 60.0)
    assert [s["label"] for s in payload["series"]] == ["V", "A", "W"]
    assert all(s["points"] == [] for s in payload["series"])
    assert payload["latest"] is None


def test_graph_payload_normalises_each_series() -> None:
    samples = [PowerSample(t=float(i), voltage=5.0, current=0.1 * i) for i in range(5)]
    payload = graph_payload(samples, 60.0)
    current = payload["series"][1]["points"]
    assert len(current) == 5
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in current)
    assert current[-1][1] == pytest.approx(1.0)  # its own range: the peak is the top
    assert payload["latest"] == pytest.approx([5.0, 0.4, 2.0])
    json.dumps(payload)


# --- server ----------------------------------------------------------------


@pytest.fixture
def served():
    hub = TopicHub()
    server = BrowserSourceServer(hub, 0)
    server.start()
    yield hub, server
    server.stop()


def _get(server: BrowserSourceServer, path: str, host: str | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    headers = {"Host": host} if host is not None else {}
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def _read_raw_event(response) -> tuple[str, str]:
    """Read one event off an SSE response as ``(name, data)``, skipping heartbeats.

    An event with no ``event:`` line is a ``message``, as it is to a browser.
    """
    name = "message"
    while True:
        line = response.fp.readline().decode()
        if not line:
            raise AssertionError("the stream ended")
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: "):
            response.fp.readline()  # the blank line that ends the event
            return name, line[6:].rstrip("\n")
        elif not line.strip():
            name = "message"


def _read_event(response) -> dict:
    """Read the next event a page's ``onmessage`` would get, as that page does."""
    while True:
        name, data = _read_raw_event(response)
        if name == "message":
            return json.loads(data)


def _open_stream(server: BrowserSourceServer, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", path)
    return conn, conn.getresponse()


_STAMP = re.compile(r'<meta name="benchhud-build" content="([0-9a-z]+)">')


def _stamp_of(page: bytes) -> str:
    found = _STAMP.findall(page.decode())
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("panel", list(PANELS))
def test_every_panel_page_is_served(served, panel) -> None:
    _, server = served
    response, body = _get(server, f"/panel/{panel}")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/html")
    assert f'subscribe("{panel}"' in body.decode()


def test_pages_are_transparent_and_load_nothing_remote(served) -> None:
    _, server = served
    _, css = _get(server, "/static/panel.css")
    assert "background: transparent" in css.decode()
    for panel in PANELS:
        _, page = _get(server, f"/panel/{panel}")
        assert "http://" not in page.decode() and "https://" not in page.decode()


def test_the_job_card_carries_a_credit_that_can_be_turned_off(served) -> None:
    """The stream says what the HUD is, in the card's corner (#494)."""
    _, server = served
    _, page = _get(server, "/panel/job")
    html = page.decode()
    assert '<div class="credit" id="credit">benchhud</div>' in html
    # Inside the card, so it shows and hides with it.
    card = html.split('id="card">', 1)[1].split("<script>", 1)[0]
    assert 'id="credit"' in card
    assert '.get("credit") === "0"' in html
    _, css = _get(server, "/static/panel.css")
    assert ".job .credit" in css.decode()


def test_hidden_beats_every_panels_own_display(served) -> None:
    """A hidden card must not be displayed, whatever its panel's rule says (#444).

    `.nowplaying { display: flex }` sat after `.hidden { display: none }` with the
    same specificity, so the banner stayed on stream as an empty box.
    """
    _, server = served
    _, body = _get(server, "/static/panel.css")
    css = re.sub(r"/\*.*?\*/", "", body.decode(), flags=re.S)
    rules = dict(re.findall(r"(?m)^([^{}@\n]+?)\s*\{([^}]*)\}", css))
    hidden = rules[".hidden"]
    assert re.search(r"display:\s*none\s*!important", hidden), hidden
    # ...and nothing else claims `display` with !important, which would tie it again.
    rivals = [
        sel
        for sel, decl in rules.items()
        if sel != ".hidden" and re.search(r"display:[^;]*!important", decl)
    ]
    assert rivals == []
    # every page that starts hidden relies on that one rule
    for panel in PANELS:
        _, page = _get(server, f"/panel/{panel}")
        for classes in re.findall(r'class="([^"]*\bhidden\b[^"]*)"', page.decode()):
            others = [c for c in classes.split() if c != "hidden"]
            assert others, "a hidden element with no panel class has nothing to override"


def test_static_assets_are_served(served) -> None:
    _, server = served
    for name, kind in (("panel.js", "text/javascript"), ("dseg7.ttf", "font/ttf")):
        response, body = _get(server, f"/static/{name}")
        assert response.status == 200
        assert response.getheader("Content-Type").startswith(kind)
        assert body


def test_index_lists_every_panel_url(served) -> None:
    _, server = served
    response, body = _get(server, "/")
    assert response.status == 200
    for panel in PANELS:
        assert server.url(panel) in body.decode()


def test_unknown_paths_are_404(served) -> None:
    _, server = served
    for path in ("/panel/customer", "/static/../config.toml", "/events/nope", "/x"):
        assert _get(server, path)[0].status == 404


def test_a_foreign_host_header_is_refused(served) -> None:
    """DNS rebinding: a page reaching our port under its own name gets nothing."""
    _, server = served
    assert _get(server, "/panel/meter", host=f"evil.example:{server.port}")[0].status == 403
    assert _get(server, "/panel/meter", host=f"localhost:{server.port}")[0].status == 200


def test_it_binds_loopback_only(served) -> None:
    _, server = served
    assert server._httpd.server_address[0] == "127.0.0.1"
    assert server.url().startswith("http://127.0.0.1:")


def test_events_send_the_current_value_then_each_new_one(served) -> None:
    hub, server = served
    hub.publish("meter", {"n": 1})
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", "/events/meter")
    response = conn.getresponse()
    assert response.getheader("Content-Type") == "text/event-stream"
    assert _read_event(response) == {"n": 1}
    hub.publish("meter", {"n": 2})
    assert _read_event(response) == {"n": 2}
    conn.close()


# --- a page left open reloads after an upgrade (#501) -----------------------


def test_the_build_is_a_short_stable_hash() -> None:
    build = server_module.page_build()
    assert re.fullmatch(r"[0-9a-f]{16}", build)
    server_module.page_build.cache_clear()
    assert server_module.page_build() == build


def test_the_build_covers_every_file_a_page_is_made_of() -> None:
    covered = set(server_module._page_files())
    assert {f"web/{name}.html" for name in PANELS} <= covered
    assert "web/terminal.html" in covered
    assert {resource for resource, _ in server_module._STATIC.values()} <= covered


@pytest.mark.parametrize("changed", server_module._page_files())
def test_changing_any_page_file_changes_the_build(monkeypatch, changed) -> None:
    before = server_module.page_build()
    real = server_module._resource
    monkeypatch.setattr(
        server_module,
        "_resource",
        lambda path: real(path) + b"\n" if path == changed else real(path),
    )
    server_module.page_build.cache_clear()
    try:
        assert server_module.page_build() != before
    finally:
        monkeypatch.undo()
        server_module.page_build.cache_clear()


def test_moving_bytes_between_files_changes_the_build(monkeypatch) -> None:
    """Each file is hashed with its name and length, not run together."""
    first, second = server_module._page_files()[:2]
    real = server_module._resource
    before = server_module.page_build()

    def moved(path: str) -> bytes:
        if path == first:
            return real(first)[:-1]
        if path == second:
            return real(first)[-1:] + real(second)
        return real(path)

    monkeypatch.setattr(server_module, "_resource", moved)
    server_module.page_build.cache_clear()
    try:
        assert server_module.page_build() != before
    finally:
        monkeypatch.undo()
        server_module.page_build.cache_clear()


@pytest.mark.parametrize("panel", list(PANELS))
def test_every_panel_page_is_stamped_with_the_servers_build(served, panel) -> None:
    _, server = served
    _, page = _get(server, f"/panel/{panel}")
    assert _stamp_of(page) == server.build == server_module.page_build()
    # The stamp is in the head, before the script that reads it.
    text = page.decode()
    assert text.index("benchhud-build") < text.index("/static/panel.js")


@pytest.mark.parametrize("panel", list(PANELS))
def test_every_feed_opens_by_naming_the_build(served, panel) -> None:
    hub, server = served
    hub.publish(panel, {"n": 1})
    conn, response = _open_stream(server, f"/events/{panel}")
    assert _read_raw_event(response) == ("build", server.build)
    assert _read_raw_event(response) == ("message", '{"n": 1}')
    conn.close()


def test_the_build_comes_even_before_anything_is_published(served) -> None:
    """A page mustn't wait for the first reading to learn it's stale."""
    _, server = served
    conn, response = _open_stream(server, "/events/job")
    assert _read_raw_event(response) == ("build", server.build)
    conn.close()


def test_the_build_is_a_named_event_an_older_page_never_sees(served) -> None:
    """A page from before this has only ``onmessage``, which gets unnamed events."""
    hub, server = served
    hub.publish("meter", {"n": 1})
    conn, response = _open_stream(server, "/events/meter")
    raw = response.fp.readline() + response.fp.readline() + response.fp.readline()
    assert raw == f"event: build\ndata: {server.build}\n\n".encode()
    assert _read_event(response) == {"n": 1}
    conn.close()


def test_after_an_upgrade_the_same_address_serves_and_announces_the_new_build(served) -> None:
    """What a page left open in OBS meets: same port, different benchhud."""
    hub, server = served
    port = server.port
    _, old_page = _get(server, "/panel/job")
    server.stop()

    upgraded = BrowserSourceServer(hub, port)
    upgraded.build = "0123456789abcdef"
    upgraded.start()
    try:
        conn, response = _open_stream(upgraded, "/events/job")
        announced = _read_raw_event(response)
        conn.close()
        assert announced == ("build", "0123456789abcdef")
        assert announced[1] != _stamp_of(old_page)  # so the old page reloads
        _, new_page = _get(upgraded, "/panel/job")
        assert _stamp_of(new_page) == announced[1]  # and the new one doesn't
    finally:
        upgraded.stop()


def test_every_page_has_one_place_for_the_stamp() -> None:
    for path in server_module._page_files():
        if path.endswith(".html"):
            assert server_module._resource(path).count(b'<meta charset="utf-8">') == 1, path


def test_a_page_with_nowhere_to_put_the_stamp_is_an_error() -> None:
    with pytest.raises(ValueError, match="charset"):
        server_module.stamp(b"<!doctype html><title>x</title>", "abc")


def test_stamping_changes_nothing_else() -> None:
    page = server_module._resource("web/job.html")
    stamped = server_module.stamp(page, "abc")
    assert stamped.replace(b'\n<meta name="benchhud-build" content="abc">', b"") == page


def test_stop_ends_open_streams_and_frees_the_port(served) -> None:
    hub, server = served
    port = server.port
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/events/graph")
    response = conn.getresponse()
    assert _read_raw_event(response) == ("build", server.build)  # the opening line
    server.stop()
    assert response.fp.read() == b""  # the stream closed rather than hanging
    conn.close()
    assert not server.running
    # The port is free again: a new server takes it and the same hub still works.
    again = BrowserSourceServer(hub, port)
    again.start()
    try:
        assert _get(again, "/panel/graph")[0].status == 200
    finally:
        again.stop()


def test_a_taken_port_raises() -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        with pytest.raises(OSError):
            BrowserSourceServer(TopicHub(), blocker.getsockname()[1]).start()


# --- shell wiring ------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def store():
    s = JobStore(":memory:")
    yield s
    s.close()


def _window(qtbot, store, *, enabled=True, port=0) -> MainWindow:
    config = Config()
    config.browser_sources.enabled = enabled
    config.browser_sources.port = port
    window = MainWindow(config, job_store=store)
    qtbot.addWidget(window)
    return window


def test_off_by_default(qtbot, store) -> None:
    window = MainWindow(Config(), job_store=store)
    qtbot.addWidget(window)
    window.start_browser_sources()
    assert window.browser_sources is None
    assert window.browser_sources_warning() is None


def test_telemetry_reaches_the_meter_and_graph_topics(qtbot, store) -> None:
    window = _window(qtbot, store)
    window.start_browser_sources()
    try:
        window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=0.25), t=1.0)
        window.on_telemetry(TelemetrySnapshot(voltage=5.0, current=0.5), t=2.0)
        _, meter = window._browser_hub.latest("meter")
        assert meter["readout"]["voltage"] == window.power_panel.voltage_text
        assert meter["readout"]["current"] == window.power_panel.current_text
        _, graph = window._browser_hub.latest("graph")
        assert graph["latest"] == pytest.approx([5.0, 0.5, 2.5])
        assert len(graph["series"][1]["points"]) == 2
    finally:
        window.stop_browser_sources()


def test_selecting_a_job_publishes_a_pii_free_card(qtbot, store) -> None:
    job = store.create_job("MacBook Pro", customer="Jane Doe", board_id="820-00239")
    window = _window(qtbot, store)
    window.start_browser_sources()
    try:
        window.jobs_panel.refresh_jobs()
        window.jobs_panel.select_job(job.id)
        _, card = window._browser_hub.latest("job")
        assert card["job"]["board_id"] == "820-00239"
        assert "Jane" not in json.dumps(card)
    finally:
        window.stop_browser_sources()


def test_a_taken_port_is_said_in_the_status_bar(qtbot, store) -> None:
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        window = _window(qtbot, store, port=port)
        window.start_browser_sources()
        assert window.browser_sources is None
        assert f"port {port}" in window.browser_sources_warning()
        assert "local server off" in window.statusBar().currentMessage()


def test_preferences_turn_it_on_and_off_without_a_relaunch(qtbot, store) -> None:
    window = _window(qtbot, store, enabled=False)
    dialog = PreferencesDialog(window._config, window._scanner)
    qtbot.addWidget(dialog)
    dialog.browser_sources_enabled.setChecked(True)
    dialog.browser_sources_port.setValue(_free_port())
    dialog.apply_to_config()
    window.restart_browser_sources()
    try:
        assert window.browser_sources is not None
        assert window._config.browser_sources.enabled
    finally:
        window._config.browser_sources.enabled = False
        window.restart_browser_sources()
    assert window.browser_sources is None


def test_close_stops_the_server(qtbot, store) -> None:
    window = _window(qtbot, store)
    window.start_browser_sources()
    server = window.browser_sources
    window.close()
    assert not server.running
