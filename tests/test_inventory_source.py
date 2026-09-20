"""Tests for the inventory seam and the bench-parts HTTP adapter.

Hardware-free, network-free: the HTTP layer is driven through an injected
fake opener, same as the ticket source's tests. The behaviour under test that
matters most is the asymmetry — reads degrade quietly, writes never do.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from benchhud.config import Config
from benchhud.inventory import (
    HttpInventorySource,
    InMemoryInventorySource,
    InventorySource,
    InventoryWriteError,
    NullInventorySource,
    Part,
)
from benchhud.inventory.http_source import _part_from_dict

_LOW = [
    {
        "id": 7,
        "description": "Dual N-ch MOSFET",
        "mpn": "IRF7910",
        "status": "low",
        "quantity_on_hand": 2,
        "min_quantity": 4,
        "category_label": "MOSFET",
        "location": "A3",
        "project": "bench",
        "unit_cost_cents": 187,
        "url": "http://parts/parts/7",
    },
    {"id": 9, "description": "PETG teal", "status": "out", "quantity_on_hand": 0},
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
    """A fake opener serving canned JSON by URL suffix, else 404s."""

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


def _source(routes, **kw):
    return HttpInventorySource("http://parts:8000/", "tok", opener=_opener(routes, **kw))


# --- parsing ---------------------------------------------------------------


def test_part_from_dict_ignores_unknown_keys_and_defaults_absent_ones() -> None:
    part = _part_from_dict({"id": 1, "description": "x", "brand_new_field": "ignored"})

    assert part.id == 1
    assert part.status == "ok"
    assert part.mpn is None


# --- reads: parse, and degrade quietly --------------------------------------


def test_list_low_stock_parses_the_below_min_list() -> None:
    src = _source({"/api/parts?below_min=1": json.dumps(_LOW)})

    parts = src.list_low_stock()

    assert [p.id for p in parts] == [7, 9]
    assert parts[0].mpn == "IRF7910"
    assert parts[0].status == "low"
    assert parts[1].status == "out"


def test_search_quotes_the_query_and_sends_the_bearer_token() -> None:
    seen: list = []
    src = _source({"/api/parts?q=100nF%20X7R": json.dumps([_LOW[0]])}, record=seen)

    parts = src.search("100nF X7R")

    assert len(parts) == 1
    assert seen[0].get_header("Authorization") == "Bearer tok"


def test_get_part_returns_none_on_404() -> None:
    src = _source({"/api/parts/7": json.dumps(_LOW[0])})

    assert src.get_part(7) is not None
    assert src.get_part(999) is None


def test_reads_degrade_to_empty_when_the_service_is_unreachable() -> None:
    src = HttpInventorySource(
        "http://parts:8000", "tok", opener=_raising_opener(urllib.error.URLError("down"))
    )

    assert src.list_low_stock() == []
    assert src.search("x") == []
    assert src.get_part(7) is None


def test_reads_degrade_on_garbage_payloads() -> None:
    src = _source({"/api/parts?below_min=1": '"not a list"', "/api/parts/7": "[]"})

    assert src.list_low_stock() == []
    assert src.get_part(7) is None


# --- consume: the write that must never fail silently -----------------------


def test_consume_posts_json_and_returns_the_updated_part() -> None:
    seen: list = []
    updated = dict(_LOW[0], quantity_on_hand=1)
    src = _source({"/api/parts/7/consume": json.dumps(updated)}, record=seen)

    part = src.consume(7, 1, ticket_ref="26-0001")

    assert part.quantity_on_hand == 1
    request = seen[0]
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"quantity": 1, "ticket_ref": "26-0001"}
    assert request.get_header("Authorization") == "Bearer tok"
    assert request.get_header("Content-type") == "application/json"


def test_consume_omits_ticket_ref_when_not_given() -> None:
    seen: list = []
    src = _source({"/api/parts/7/consume": json.dumps(_LOW[0])}, record=seen)

    src.consume(7, 2)

    assert json.loads(seen[0].data) == {"quantity": 2}


def test_consume_raises_with_the_servers_reason() -> None:
    class _WithBody(urllib.error.HTTPError):
        def read(self) -> bytes:
            return json.dumps({"detail": "insufficient stock: 2 on hand, 5 requested"}).encode()

    def _refuse_with_detail(request, _timeout):
        raise _WithBody(request.full_url, 400, "Bad Request", {}, None)

    src = HttpInventorySource("http://parts:8000", "tok", opener=_refuse_with_detail)

    with pytest.raises(InventoryWriteError, match="insufficient stock"):
        src.consume(7, 5)


def test_consume_raises_when_unreachable_and_never_swallows() -> None:
    src = HttpInventorySource(
        "http://parts:8000", "tok", opener=_raising_opener(urllib.error.URLError("down"))
    )

    with pytest.raises(InventoryWriteError, match="unreachable"):
        src.consume(7, 1)


def test_consume_raises_on_a_bare_http_error_with_the_status() -> None:
    src = HttpInventorySource(
        "http://parts:8000",
        "tok",
        opener=_raising_opener(urllib.error.HTTPError("u", 401, "nope", {}, None)),
    )

    with pytest.raises(InventoryWriteError, match="401"):
        src.consume(7, 1)


# --- the seam: null + in-memory + protocol conformance ----------------------


def test_every_source_satisfies_the_protocol() -> None:
    for src in (NullInventorySource(), InMemoryInventorySource(), _source({})):
        assert isinstance(src, InventorySource)


def test_null_source_reads_empty_and_refuses_writes() -> None:
    src = NullInventorySource()

    assert src.list_low_stock() == []
    assert src.get_part(1) is None
    with pytest.raises(InventoryWriteError, match="no inventory backend"):
        src.consume(1, 1)


def test_in_memory_source_consumes_and_refuses_overdraw() -> None:
    src = InMemoryInventorySource([Part(id=1, description="cap", quantity_on_hand=3)])

    assert src.consume(1, 2).quantity_on_hand == 1
    assert src.get_part(1).quantity_on_hand == 1
    with pytest.raises(InventoryWriteError, match="insufficient"):
        src.consume(1, 5)


def test_in_memory_search_matches_description_and_mpn() -> None:
    src = InMemoryInventorySource(
        [Part(id=1, description="Dual MOSFET", mpn="IRF7910"), Part(id=2, description="cap")]
    )

    assert [p.id for p in src.search("irf")] == [1]
    assert [p.id for p in src.search("MOSFET")] == [1]


# --- config -----------------------------------------------------------------


def test_inventory_config_defaults_to_disabled_and_loads_from_toml() -> None:
    assert Config().inventory.base_url == ""

    cfg = Config.from_dict({"inventory": {"base_url": "http://parts:8000", "token": "tok"}})

    assert cfg.inventory.base_url == "http://parts:8000"
    assert cfg.inventory.token == "tok"


def test_inventory_config_round_trips_through_to_dict() -> None:
    cfg = Config()
    cfg.inventory.base_url = "http://parts:8000"

    assert Config.from_dict(cfg.to_dict()).inventory.base_url == "http://parts:8000"
