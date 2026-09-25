"""OBS browser sources (#180): the hub, the payloads, the server and the shell wiring.

The server tests talk real HTTP to a real listener on an ephemeral loopback
port, including an SSE stream, so what's checked is what OBS will receive.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading

import pytest

from benchhud.browser_sources import PANELS, BrowserSourceServer, TopicHub
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


def _read_event(response) -> dict:
    """Read one ``data:`` event off an SSE response, skipping heartbeats."""
    while True:
        line = response.fp.readline().decode()
        if line.startswith("data: "):
            response.fp.readline()  # the blank line that ends the event
            return json.loads(line[6:])


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


def test_stop_ends_open_streams_and_frees_the_port(served) -> None:
    hub, server = served
    port = server.port
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/events/graph")
    response = conn.getresponse()
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
