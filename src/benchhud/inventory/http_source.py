"""An :class:`InventorySource` backed by the bench-parts HTTP API.

bench-parts exposes a bearer-authed, LAN-only parts API::

    GET  /api/parts?below_min=1        -> [Part, ...]  (the "to order" list)
    GET  /api/parts?q=<query>          -> [Part, ...]
    GET  /api/parts/{id}               -> Part | 404
    POST /api/parts/{id}/consume       -> updated Part  (records a movement)

Reads follow :mod:`benchhud.tickets.http_source` exactly: stdlib ``urllib``,
an injectable opener for tests, and graceful degradation — an unreachable
service or a bad payload is an empty list / ``None`` plus a log line, never a
crash in the HUD.

``consume`` is deliberately different. It writes into the shop's movements
ledger, and a write that silently vanished would leave the shelf lying to the
next job. Every failure raises :class:`InventoryWriteError` with the reason
bench-parts gave (insufficient stock, retired part, bad token, unreachable),
and nothing here retries: consume is not idempotent, so a retried timeout
could double-count real stock.

Always pass the active job's ``ticket_ref`` — bench-parts snapshots the unit
cost onto each movement, so ``/api/movements?ticket_ref=`` becomes the job's
parts bill for free.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from benchhud.inventory.source import InventoryWriteError, Part

log = logging.getLogger(__name__)

# Keys benchhud understands. Extra keys in the payload are ignored
# (forward-compatible — bench-parts adds fields over time), and absent
# optional keys fall back to Part's defaults.
_PART_KEYS = (
    "id",
    "description",
    "status",
    "quantity_on_hand",
    "min_quantity",
    "mpn",
    "category_label",
    "location",
    "project",
    "unit_cost_cents",
    "url",
)

#: Opens a prepared request and returns a response context manager. Injectable
#: so tests drive the source without a network.
Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _part_from_dict(data: dict[str, Any]) -> Part:
    return Part(**{key: data[key] for key in _PART_KEYS if key in data})


@dataclass
class HttpInventorySource:
    """Read stock from, and record consumption against, a bench-parts service."""

    base_url: str
    token: str
    name: str = "bench-parts"
    timeout: float = 5.0
    opener: Opener = _default_opener

    def list_low_stock(self) -> list[Part]:
        return self._get_parts("/api/parts?below_min=1")

    def search(self, query: str) -> list[Part]:
        return self._get_parts("/api/parts?q=" + urllib.parse.quote(query))

    def get_part(self, part_id: int) -> Part | None:
        payload = self._get(f"/api/parts/{part_id}")
        if not isinstance(payload, dict):
            return None
        return _part_from_dict(payload)

    def consume(self, part_id: int, quantity: int, ticket_ref: str | None = None) -> Part:
        """Record parts used on a job. Raises on *any* failure — see module doc."""
        body: dict[str, Any] = {"quantity": quantity}
        if ticket_ref:
            body["ticket_ref"] = ticket_ref
        request = urllib.request.Request(
            self.base_url.rstrip("/") + f"/api/parts/{part_id}/consume",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request, self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise InventoryWriteError(_http_error_reason(exc)) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise InventoryWriteError(f"bench-parts unreachable: {exc}") from exc
        if not isinstance(payload, dict):
            raise InventoryWriteError("bench-parts sent an unexpected response")
        return _part_from_dict(payload)

    def _get_parts(self, path: str) -> list[Part]:
        payload = self._get(path)
        if not isinstance(payload, list):
            return []  # unreachable / error / unexpected shape → empty list
        return [_part_from_dict(item) for item in payload if isinstance(item, dict)]

    def _get(self, path: str) -> Any:
        """GET + parse JSON, or ``None`` on any failure (never raises)."""
        url = self.base_url.rstrip("/") + path
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with self.opener(request, self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # 404 is the normal "no such part" miss; log anything else (401 bad
            # token, 5xx) but still don't crash the HUD.
            if exc.code != 404:
                log.warning("bench-parts %s -> HTTP %s", path, exc.code)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("bench-parts unreachable or bad response (%s): %s", url, exc)
            return None


def _http_error_reason(exc: urllib.error.HTTPError) -> str:
    """The server's ``detail`` when it sent one, else the bare status.

    bench-parts puts the human-readable reason ("insufficient stock: …",
    "part is retired") in JSON ``detail`` — that's the message worth showing
    the operator, not "HTTP 400".
    """
    try:
        detail = json.loads(exc.read()).get("detail")
    except (ValueError, AttributeError, OSError):
        detail = None
    if isinstance(detail, str) and detail:
        return detail
    return f"bench-parts refused the write (HTTP {exc.code})"
