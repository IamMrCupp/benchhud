"""A :class:`TicketSource` backed by the benchhud-intake HTTP API.

intake exposes a read-only, LAN-only, bearer-authed ticket API::

    GET /api/tickets?status=open   -> [Ticket, ...]
    GET /api/tickets/{ref}         -> Ticket | 404

This maps those onto the :class:`~benchhud.tickets.source.TicketSource` seam
using the stdlib ``urllib`` — no new dependency. It never raises into the HUD:
an unreachable bench, an auth failure, or a bad payload degrades to an empty
queue / ``None`` plus a log line, so a flaky LAN can't take the bench down.

The ``customer`` field it carries is sensitive — see the PII note on
:class:`~benchhud.tickets.source.Ticket`; only ``board_id`` and the other
non-PII fields are safe to composite to the virtual camera.
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

from benchhud.tickets.source import Ticket

log = logging.getLogger(__name__)

# Keys benchhud understands. Extra keys in the payload are ignored
# (forward-compatible), and absent optional keys fall back to Ticket's defaults
# (so a response from an intake not yet sending board_id still parses).
_TICKET_KEYS = ("ref", "device", "symptoms", "customer", "status", "url", "board_id")

#: Opens a prepared request and returns a response context manager. Injectable
#: so tests drive the source without a network.
Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _ticket_from_dict(data: dict[str, Any]) -> Ticket:
    return Ticket(**{key: data[key] for key in _TICKET_KEYS if key in data})


@dataclass
class HttpTicketSource:
    """Pull the bench work queue from the intake API (read-only)."""

    base_url: str
    token: str
    name: str = "intake"
    timeout: float = 5.0
    opener: Opener = _default_opener

    def list_open_tickets(self) -> list[Ticket]:
        payload = self._get("/api/tickets?status=open")
        if not isinstance(payload, list):
            return []  # unreachable / error / unexpected shape → empty queue
        return [_ticket_from_dict(item) for item in payload if isinstance(item, dict)]

    def get_ticket(self, ref: str) -> Ticket | None:
        payload = self._get(f"/api/tickets/{urllib.parse.quote(ref)}")
        if not isinstance(payload, dict):
            return None
        return _ticket_from_dict(payload)

    def _get(self, path: str) -> Any:
        """GET + parse JSON, or ``None`` on any failure (never raises)."""
        url = self.base_url.rstrip("/") + path
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with self.opener(request, self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # 404 is the normal "no such ticket" miss; log anything else (401 bad
            # token, 5xx) but still don't crash the HUD.
            if exc.code != 404:
                log.warning("intake %s -> HTTP %s", path, exc.code)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("intake unreachable or bad response (%s): %s", url, exc)
            return None
