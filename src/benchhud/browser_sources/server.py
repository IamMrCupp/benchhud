"""The loopback HTTP server behind the OBS Browser sources.

Stdlib only (``http.server``), on its own threads, so it adds no dependency and
never touches the GUI thread. Routes:

* ``/`` — an index of the panel URLs, to copy into OBS.
* ``/panel/<name>`` — one page per panel (:data:`PANELS`), transparent so it
  layers over anything in a scene.
* ``/events/<name>`` — that panel's live feed as Server-Sent Events. SSE is
  one-directional, which is all a display needs, and ``EventSource``
  reconnects on its own when benchhud restarts.
* ``/panel/nowplaying/art`` — the current track's artwork (#251): only the file
  the media player reported, and only if it's an image.
* ``/static/<file>`` — the shared script, stylesheet, the DSEG font and the
  vendored xterm.js. Nothing loads from a CDN, so a page works with the bench
  offline.
* ``/terminal?token=…`` and ``/terminal/events?token=…`` — the terminal page and
  its feed (#194), when a :class:`~benchhud.terminal.TerminalHost` is attached.
  The in-app token may also ``POST /terminal/input`` and ``/terminal/resize``;
  the share token only watches. Posts must come from this origin.

The data panels (:data:`PANELS`) and the terminal are switched separately: the
server can run for the terminal alone, and then the panels 404.

**Loopback only, and the Host header must say so.** Binding to ``127.0.0.1``
keeps other machines out; checking ``Host`` keeps out a web page in a local
browser that uses DNS rebinding to reach the port under its own name.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from benchhud.browser_sources.hub import TopicHub

if TYPE_CHECKING:
    from benchhud.terminal.host import TerminalHost

log = logging.getLogger(__name__)

#: Panel name → page title. Each is a page, an SSE topic and a hub topic.
PANELS: dict[str, str] = {
    "meter": "Meter",
    "graph": "Power graph",
    "job": "Job card",
    "nowplaying": "Now playing",
}

HOST = "127.0.0.1"

#: How often an idle SSE stream sends a comment, so a dead client is noticed
#: (the write fails) and its thread exits.
HEARTBEAT_SECONDS = 15.0

_STATIC = {
    "panel.css": ("web/panel.css", "text/css; charset=utf-8"),
    "panel.js": ("web/panel.js", "text/javascript; charset=utf-8"),
    "dseg7.ttf": ("fonts/DSEG7Classic-Bold.ttf", "font/ttf"),
    "xterm.js": ("web/vendor/xterm.js", "text/javascript; charset=utf-8"),
    "xterm.css": ("web/vendor/xterm.css", "text/css; charset=utf-8"),
    "addon-fit.js": ("web/vendor/addon-fit.js", "text/javascript; charset=utf-8"),
}

#: Largest keystroke batch or resize body accepted, in bytes. A paste is fine.
MAX_POST_BYTES = 256 * 1024


def _resource(path: str) -> bytes:
    return files("benchhud.resources").joinpath(*path.split("/")).read_bytes()


class BrowserSourceServer:
    """Serve :data:`PANELS` from ``hub`` on ``127.0.0.1:port``.

    ``port=0`` picks a free port (tests); :attr:`port` reports the real one.
    :meth:`start` raises ``OSError`` when the port is taken, so the caller can
    say so instead of streaming nothing.
    """

    def __init__(
        self,
        hub: TopicHub,
        port: int,
        *,
        panels: bool = True,
        terminal: TerminalHost | None = None,
        now_playing_art: Callable[[], tuple[bytes, str] | None] | None = None,
    ) -> None:
        self._hub = hub
        self.panels_enabled = panels
        self.terminal = terminal
        #: The now-playing artwork as (bytes, content type), or None (#251).
        self.now_playing_art = now_playing_art
        self._requested_port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self._requested_port
        return self._httpd.server_address[1]

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def origin(self) -> str:
        return f"http://{HOST}:{self.port}"

    def url(self, panel: str = "") -> str:
        return f"{self.origin}/panel/{panel}" if panel else f"{self.origin}/"

    def terminal_url(self, token: str) -> str:
        return f"{self.origin}/terminal?token={token}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((HOST, self._requested_port), _handler_for(self._hub, self))
        httpd.daemon_threads = True
        self._stopping.clear()
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="browser-sources", daemon=True
        )
        self._thread.start()
        log.info("browser sources on %s", self.url())

    def stop(self) -> None:
        """Stop serving and release every open event stream (idempotent)."""
        if self._httpd is None:
            return
        self._stopping.set()
        self._hub.wake_all()  # so the SSE threads see it and return
        if self.terminal is not None:
            self.terminal.wake_all()
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._httpd = None
        self._thread = None


def _handler_for(hub: TopicHub, server: BrowserSourceServer) -> type[BaseHTTPRequestHandler]:
    class Handler(_Handler):
        pass

    Handler.hub = hub
    Handler.owner = server
    return Handler


class _Handler(BaseHTTPRequestHandler):
    hub: TopicHub
    owner: BrowserSourceServer
    server_version = "benchhud"

    def log_message(self, format: str, *args) -> None:
        log.debug("browser source: " + format, *args)

    def do_GET(self) -> None:
        if not self._host_ok():
            self.send_error(HTTPStatus.FORBIDDEN, "loopback only")
            return
        path = urlsplit(self.path).path
        panels = self.owner.panels_enabled
        if path == "/" and panels:
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", self._index().encode())
        elif path == "/panel/nowplaying/art" and panels:
            art = self.owner.now_playing_art() if self.owner.now_playing_art else None
            if art is None:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._send(HTTPStatus.OK, art[1], art[0])
        elif path.startswith("/panel/") and path[7:] in PANELS and panels:
            page = _resource(f"web/{path[7:]}.html")
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", page)
        elif path.startswith("/events/") and path[8:] in PANELS and panels:
            self._stream(path[8:])
        elif path in ("/terminal", "/terminal/events"):
            mode = self._terminal_mode()
            if mode is None:
                self.send_error(HTTPStatus.FORBIDDEN, "bad or missing token")
            elif path == "/terminal":
                page = _resource("web/terminal.html")
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", page)
            else:
                self._terminal_stream(mode)
        elif path.startswith("/static/") and path[8:] in _STATIC:
            resource, content_type = _STATIC[path[8:]]
            self._send(HTTPStatus.OK, content_type, _resource(resource))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if not self._host_ok() or not self._origin_ok():
            self.send_error(HTTPStatus.FORBIDDEN, "loopback only")
            return
        if path not in ("/terminal/input", "/terminal/resize"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self._terminal_mode() != "write":
            self.send_error(HTTPStatus.FORBIDDEN, "read-only")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_POST_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        terminal = self.owner.terminal
        assert terminal is not None  # a write token implies a terminal
        if path == "/terminal/input":
            terminal.write(body)
        else:
            try:
                size = json.loads(body)
                terminal.resize(int(size["cols"]), int(size["rows"]))
            except (ValueError, KeyError, TypeError):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _terminal_mode(self) -> str | None:
        """What this request's token may do with the terminal, if anything."""
        terminal = self.owner.terminal
        if terminal is None:
            return None
        token = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
        return terminal.token_mode(token)

    def _origin_ok(self) -> bool:
        """A browser always sends Origin on a POST; it must be this server."""
        origin = self.headers.get("Origin")
        port = self.owner.port
        return origin is None or origin in {f"http://{HOST}:{port}", f"http://localhost:{port}"}

    def _host_ok(self) -> bool:
        port = self.owner.port
        return self.headers.get("Host", "") in {f"{HOST}:{port}", f"localhost:{port}"}

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _index(self) -> str:
        rows = "".join(
            f'<li>{title}: <a href="/panel/{name}">{self.owner.url(name)}</a></li>'
            for name, title in PANELS.items()
        )
        return (
            "<!doctype html><meta charset=utf-8><title>benchhud browser sources</title>"
            "<body style='font-family:sans-serif'><h1>benchhud browser sources</h1>"
            f"<p>Add each URL to OBS as a Browser source.</p><ul>{rows}</ul>"
        )

    def _start_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True

    def _terminal_stream(self, mode: str) -> None:
        """Feed a terminal page until it goes away or the server stops."""
        from benchhud.terminal.events import terminal_events

        terminal = self.owner.terminal
        assert terminal is not None
        self._start_stream()
        stopping = lambda: self.owner.stopping  # noqa: E731
        try:
            for event in terminal_events(terminal, mode, stopping, HEARTBEAT_SECONDS):
                if self.owner.stopping:
                    break
                if event is None:
                    self.wfile.write(b": ping\n\n")
                else:
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _stream(self, topic: str) -> None:
        """Send ``topic``'s latest payload now, then each newer one as it lands."""
        self._start_stream()
        version = 0
        try:
            while not self.owner.stopping:
                latest, payload = self.hub.wait(topic, version, HEARTBEAT_SECONDS)
                if self.owner.stopping:
                    break
                if latest > version:
                    version = latest
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the source was removed or OBS closed; nothing to clean up
