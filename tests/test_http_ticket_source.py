"""Tests for the intake-backed HTTP ticket source (hardware-free, no network).

The HTTP layer is driven through an injected fake opener, so nothing here
touches a socket; parsing and graceful-degradation are exercised directly.
"""

from __future__ import annotations

import json
import urllib.error

from benchhud.config import Config
from benchhud.shell.main_window import MainWindow
from benchhud.tickets import HttpTicketSource, NullTicketSource
from benchhud.tickets.http_source import _ticket_from_dict

_OPEN_QUEUE = [
    {
        "ref": "26-0001",
        "device": "CDJ-3000",
        "symptoms": "jog wheel dead",
        "customer": "DJ in LA",
        "status": "diagnosing",
        "url": "http://intake/tickets/26-0001",
        "board_id": "820-00239",
    },
    {"ref": "26-0002", "device": "iPhone 12", "symptoms": "no image", "status": "intake"},
]


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _opener(routes: dict[str, str], *, record: list | None = None):
    """A fake opener that serves canned JSON by URL suffix, else 404s."""

    def _open(request, _timeout):
        if record is not None:
            record.append(request)
        for suffix, body in routes.items():
            if request.full_url.endswith(suffix):
                return _FakeResponse(body)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    return _open


def _raising_opener(exc: Exception):
    def _open(_request, _timeout):
        raise exc

    return _open


# --- parsing -------------------------------------------------------------


def test_ticket_from_dict_maps_known_keys_and_ignores_extras() -> None:
    ticket = _ticket_from_dict({**_OPEN_QUEUE[0], "unknown": "ignored"})
    assert ticket.ref == "26-0001"
    assert ticket.board_id == "820-00239"
    assert ticket.customer == "DJ in LA"


def test_ticket_from_dict_defaults_absent_optionals() -> None:
    ticket = _ticket_from_dict(_OPEN_QUEUE[1])
    assert ticket.board_id is None
    assert ticket.customer == ""


# --- list_open_tickets ---------------------------------------------------


def test_list_open_tickets_parses_the_queue() -> None:
    source = HttpTicketSource(
        "http://intake:8000",
        "tok",
        opener=_opener({"/api/tickets?status=open": json.dumps(_OPEN_QUEUE)}),
    )
    tickets = source.list_open_tickets()
    assert [t.ref for t in tickets] == ["26-0001", "26-0002"]
    assert tickets[0].board_id == "820-00239"


def test_list_open_tickets_sends_bearer_token() -> None:
    record: list = []
    source = HttpTicketSource(
        "http://intake:8000",
        "s3cret",
        opener=_opener({"?status=open": json.dumps([])}, record=record),
    )
    source.list_open_tickets()
    assert record[0].get_header("Authorization") == "Bearer s3cret"


def test_list_open_tickets_empty_when_unreachable() -> None:
    source = HttpTicketSource(
        "http://intake:8000", "tok", opener=_raising_opener(urllib.error.URLError("down"))
    )
    assert source.list_open_tickets() == []


# --- get_ticket ----------------------------------------------------------


def test_get_ticket_parses_one() -> None:
    source = HttpTicketSource(
        "http://intake:8000",
        "tok",
        opener=_opener({"/api/tickets/26-0001": json.dumps(_OPEN_QUEUE[0])}),
    )
    ticket = source.get_ticket("26-0001")
    assert ticket is not None
    assert ticket.device == "CDJ-3000"


def test_get_ticket_404_is_none() -> None:
    source = HttpTicketSource("http://intake:8000", "tok", opener=_opener({}))
    assert source.get_ticket("missing") is None


def test_get_ticket_none_when_unreachable() -> None:
    source = HttpTicketSource("http://intake:8000", "tok", opener=_raising_opener(OSError("boom")))
    assert source.get_ticket("26-0001") is None


def test_trailing_slash_in_base_url_is_handled() -> None:
    record: list = []
    source = HttpTicketSource(
        "http://intake:8000/",
        "tok",
        opener=_opener({"/api/tickets?status=open": json.dumps([])}, record=record),
    )
    source.list_open_tickets()
    assert record[0].full_url == "http://intake:8000/api/tickets?status=open"


# --- config + MainWindow wiring ------------------------------------------


def test_intake_config_round_trips() -> None:
    cfg = Config()
    cfg.intake.base_url = "http://intake:8000"
    cfg.intake.token = "abc"
    restored = Config.from_dict(cfg.to_dict())
    assert restored.intake.base_url == "http://intake:8000"
    assert restored.intake.token == "abc"


def test_window_uses_null_source_without_intake_config(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window._ticket_source, NullTicketSource)


def test_window_builds_http_source_when_configured(qtbot) -> None:
    cfg = Config()
    cfg.intake.base_url = "http://intake:8000"
    cfg.intake.token = "tok"
    window = MainWindow(cfg)
    qtbot.addWidget(window)
    assert isinstance(window._ticket_source, HttpTicketSource)


def test_reconfigure_ticket_source_picks_up_config_change(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window._ticket_source, NullTicketSource)
    window._config.intake.base_url = "http://intake:8000"
    window.reconfigure_ticket_source()
    assert isinstance(window._ticket_source, HttpTicketSource)
