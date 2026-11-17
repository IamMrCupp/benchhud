"""Find the shop services running on this machine.

benchhud-intake and bench-parts each answer ``/healthz`` with their own name,
so a probe of the ports they're known to use tells Preferences where they are
without the owner typing a URL. Loopback only, a GET with no credentials, and
nothing is trusted but the name: the token still has to come from the owner,
or from ``bench-setup``, because nothing hands one out unauthenticated.

bench-recon is deliberately not here. It holds client engagement data and
stays off until asked for; the HUD never goes looking for it.

Qt-free.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: bench-suite's port for each service, then the port a stand-alone install
#: used before the three were given one each.
INTAKE = "benchhud-intake"
PARTS = "bench-parts"
KNOWN_PORTS: dict[str, tuple[int, ...]] = {
    INTAKE: (8401, 8000),
    PARTS: (8402, 8000),
}

#: Refused connections return at once; this only bounds a port that accepts
#: and then says nothing.
TIMEOUT_S = 0.5

Fetch = Callable[[str, float], Any]


@dataclass(frozen=True)
class LocalService:
    app: str
    version: str
    base_url: str


def _fetch(url: str, timeout: float) -> Any:
    # No proxies: this is 127.0.0.1, and a proxy from the environment would
    # send the request somewhere else.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return json.loads(response.read(65536).decode("utf-8"))


def find(app: str, *, fetch: Fetch = _fetch, timeout: float = TIMEOUT_S) -> LocalService | None:
    """The first known port where ``app`` answers as itself, or None.

    Something else on the port, a wrong name, junk, or nothing listening all
    count as not found. Never raises.
    """
    for port in KNOWN_PORTS.get(app, ()):
        base_url = f"http://127.0.0.1:{port}"
        try:
            body = fetch(f"{base_url}/healthz", timeout)
        except (OSError, ValueError):
            continue
        if isinstance(body, dict) and body.get("app") == app:
            return LocalService(app, str(body.get("version", "")), base_url)
    return None
