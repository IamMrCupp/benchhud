"""An :class:`AssetSource` backed by the bench-parts HTTP API.

bench-parts serves durable assets on the same bearer-authed, LAN-only service as
parts::

    GET /api/assets[?assigned_ref=<ref>|asset_type=<slug>]  -> [Asset, ...]
    GET /api/assets/{id}                                    -> Asset | 404

Reads follow :mod:`benchhud.inventory.http_source` exactly: stdlib ``urllib``,
an injectable opener for tests, and graceful degradation — an unreachable
service or a bad payload is an empty list / ``None`` plus a log line, never a
crash in the HUD. Read-only: there is no asset write here (assign/return belong
to the shop tablet).
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

from benchhud.assets.source import Asset

log = logging.getLogger(__name__)

# Keys benchhud understands. Extra keys are ignored (forward-compatible), and
# absent optional keys fall back to Asset's defaults.
_ASSET_KEYS = (
    "id",
    "name",
    "asset_type",
    "asset_type_label",
    "manufacturer",
    "model",
    "serial",
    "location",
    "status",
    "assigned_ref",
    "url",
)

#: Opens a prepared request and returns a response context manager. Injectable
#: so tests drive the source without a network.
Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _asset_from_dict(data: dict[str, Any]) -> Asset:
    return Asset(**{key: data[key] for key in _ASSET_KEYS if key in data})


@dataclass
class HttpAssetSource:
    """Read the durable-asset registry from a bench-parts service (read-only)."""

    base_url: str
    token: str
    name: str = "bench-parts"
    timeout: float = 5.0
    opener: Opener = _default_opener

    def list_assets(
        self, assigned_ref: str | None = None, asset_type: str | None = None
    ) -> list[Asset]:
        query = {}
        if assigned_ref is not None:
            query["assigned_ref"] = assigned_ref
        if asset_type is not None:
            query["asset_type"] = asset_type
        path = "/api/assets"
        if query:
            path += "?" + urllib.parse.urlencode(query)
        payload = self._get(path)
        if not isinstance(payload, list):
            return []  # unreachable / error / unexpected shape → empty list
        return [_asset_from_dict(item) for item in payload if isinstance(item, dict)]

    def get_asset(self, asset_id: int) -> Asset | None:
        payload = self._get(f"/api/assets/{asset_id}")
        if not isinstance(payload, dict):
            return None
        return _asset_from_dict(payload)

    def _get(self, path: str) -> Any:
        """GET + parse JSON, or ``None`` on any failure (never raises)."""
        url = self.base_url.rstrip("/") + path
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with self.opener(request, self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # 404 is the normal "no such asset" miss; log anything else (401 bad
            # token, 5xx) but still don't crash the HUD.
            if exc.code != 404:
                log.warning("bench-parts %s -> HTTP %s", path, exc.code)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("bench-parts unreachable or bad response (%s): %s", url, exc)
            return None
