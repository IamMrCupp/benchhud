"""A :class:`ReconSource` backed by the bench-recon HTTP API.

bench-recon exposes a read-only, LAN-only, bearer-authed projection::

    GET /api/engagements             -> [Engagement, ...]   (ref-safe projection)
    GET /api/engagements/{ref}       -> Engagement | 404
    GET /api/engagements/{ref}/artifacts -> [Artifact, ...]  (metadata only)

This maps those onto the :class:`~benchhud.recon.source.ReconSource` seam using
the stdlib ``urllib`` — no new dependency — exactly like
:class:`~benchhud.tickets.http_source.HttpTicketSource`. It never raises into
the HUD: an unreachable bench, an auth failure, or a bad payload degrades to an
empty list / ``None`` plus a log line, so a flaky LAN can't take the bench down.

The API only ever returns the ref-safe projection; the sensitive engagement
detail never leaves bench-recon, so there is nothing here to filter. The
artifact endpoint returns metadata (names, sizes, status), never contents.
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

from benchhud.recon.source import Artifact, Engagement

log = logging.getLogger(__name__)

# Keys benchhud understands. Extra keys are ignored (forward-compatible), and
# absent optional keys fall back to the dataclass defaults (so a projection that
# omits, say, target_class still parses).
_ENGAGEMENT_KEYS = ("ref", "title", "target_class", "status")
_ARTIFACT_KEYS = ("name", "kind", "size", "status")

#: Opens a prepared request and returns a response context manager. Injectable
#: so tests drive the source without a network.
Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _engagement_from_dict(data: dict[str, Any]) -> Engagement:
    return Engagement(**{key: data[key] for key in _ENGAGEMENT_KEYS if key in data})


def _artifact_from_dict(data: dict[str, Any]) -> Artifact:
    return Artifact(**{key: data[key] for key in _ARTIFACT_KEYS if key in data})


@dataclass
class HttpReconSource:
    """Pull the ref-safe engagement projection from bench-recon (read-only)."""

    base_url: str
    token: str
    name: str = "bench-recon"
    timeout: float = 5.0
    opener: Opener = _default_opener

    def list_engagements(self) -> list[Engagement]:
        payload = self._get("/api/engagements")
        if not isinstance(payload, list):
            return []  # unreachable / error / unexpected shape → empty list
        return [_engagement_from_dict(item) for item in payload if isinstance(item, dict)]

    def get_engagement(self, ref: str) -> Engagement | None:
        payload = self._get(f"/api/engagements/{urllib.parse.quote(ref)}")
        if not isinstance(payload, dict):
            return None
        return _engagement_from_dict(payload)

    def artifacts(self, ref: str) -> list[Artifact]:
        payload = self._get(f"/api/engagements/{urllib.parse.quote(ref)}/artifacts")
        if not isinstance(payload, list):
            return []
        return [_artifact_from_dict(item) for item in payload if isinstance(item, dict)]

    def _get(self, path: str) -> Any:
        """GET + parse JSON, or ``None`` on any failure (never raises)."""
        url = self.base_url.rstrip("/") + path
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with self.opener(request, self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # 404 is the normal "no such engagement" miss; log anything else (401
            # bad token, 5xx) but still don't crash the HUD.
            if exc.code != 404:
                log.warning("bench-recon %s -> HTTP %s", path, exc.code)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("bench-recon unreachable or bad response (%s): %s", url, exc)
            return None
