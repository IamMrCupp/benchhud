"""Finding benchhud-intake and bench-parts on this machine (Qt-free)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from benchhud import local_services
from benchhud.local_services import INTAKE, PARTS, LocalService, find


def _fetcher(answers: dict[str, object]):
    seen: list[str] = []

    def fetch(url: str, timeout: float):
        seen.append(url)
        answer = answers.get(url, ConnectionRefusedError())
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch, seen


def test_finds_intake_on_its_bench_suite_port() -> None:
    fetch, seen = _fetcher(
        {"http://127.0.0.1:8401/healthz": {"status": "ok", "app": INTAKE, "version": "0.4.1"}}
    )
    assert find(INTAKE, fetch=fetch) == LocalService(INTAKE, "0.4.1", "http://127.0.0.1:8401")
    assert seen == ["http://127.0.0.1:8401/healthz"], "stops at the first match"


def test_falls_back_to_the_old_stand_alone_port() -> None:
    fetch, _ = _fetcher({"http://127.0.0.1:8000/healthz": {"app": PARTS, "version": "0.5.1"}})
    assert find(PARTS, fetch=fetch) == LocalService(PARTS, "0.5.1", "http://127.0.0.1:8000")


def test_another_app_on_the_port_is_not_a_match() -> None:
    # Port 8000 is shared history: intake there must not be taken for parts.
    fetch, _ = _fetcher({"http://127.0.0.1:8000/healthz": {"app": INTAKE, "version": "0.3.0"}})
    assert find(PARTS, fetch=fetch) is None


def test_junk_and_silence_are_not_found() -> None:
    fetch, _ = _fetcher(
        {
            "http://127.0.0.1:8401/healthz": ValueError("not json"),
            "http://127.0.0.1:8000/healthz": ["a", "list"],
        }
    )
    assert find(INTAKE, fetch=fetch) is None
    assert find(INTAKE, fetch=_fetcher({})[0]) is None
    assert find("bench-recon", fetch=_fetcher({})[0]) is None


def test_recon_is_never_looked_for() -> None:
    assert set(local_services.KNOWN_PORTS) == {INTAKE, PARTS}


def test_real_fetch_against_a_local_server(monkeypatch) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"status": "ok", "app": INTAKE, "version": "9.9"}).encode()
            self.send_response(200 if self.path == "/healthz" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        monkeypatch.setitem(local_services.KNOWN_PORTS, INTAKE, (port,))
        # A proxy in the environment must not be used for loopback.
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
        assert find(INTAKE) == LocalService(INTAKE, "9.9", f"http://127.0.0.1:{port}")
    finally:
        server.shutdown()
        server.server_close()
