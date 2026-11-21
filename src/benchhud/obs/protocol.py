"""The obs-websocket (v5) messages benchhud uses, and where OBS keeps its password.

Qt-free, so the wire format and the password lookup are testable on their own.
benchhud only ever asks which scenes exist, which one is live, and for a change
of scene; everything else OBS can do is left to OBS.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: Message kinds ("op" codes) in the protocol.
HELLO, IDENTIFY, IDENTIFIED, EVENT, REQUEST, RESPONSE = 0, 1, 2, 5, 6, 7

#: The one event group benchhud listens to: scene changes.
SCENE_EVENTS = 1 << 2

RPC_VERSION = 1


def auth_response(password: str, salt: str, challenge: str) -> str:
    """The answer to OBS's challenge, as its protocol defines it."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode()).digest()).decode()


def identify(hello: Mapping[str, Any], password: str) -> str:
    """The Identify message answering ``hello`` (the data of OBS's Hello)."""
    data: dict[str, Any] = {"rpcVersion": RPC_VERSION, "eventSubscriptions": SCENE_EVENTS}
    auth = hello.get("authentication")
    if auth:
        data["authentication"] = auth_response(password, auth["salt"], auth["challenge"])
    return json.dumps({"op": IDENTIFY, "d": data})


def request(request_type: str, request_id: str, data: Mapping[str, Any] | None = None) -> str:
    body: dict[str, Any] = {"requestType": request_type, "requestId": request_id}
    if data:
        body["requestData"] = dict(data)
    return json.dumps({"op": REQUEST, "d": body})


def obs_websocket_config_path(platform: str = sys.platform) -> Path:
    """Where OBS on this machine keeps its WebSocket server's settings."""
    tail = Path("obs-studio") / "plugin_config" / "obs-websocket" / "config.json"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / tail
    if platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / tail
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / tail


def local_password(path: Path | None = None) -> str:
    """The password of the OBS on this machine, or "" when it can't be read.

    Read from OBS's own file so it needn't be copied into benchhud's config.
    """
    try:
        settings = json.loads((path or obs_websocket_config_path()).read_text("utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(settings, dict) or not settings.get("auth_required", True):
        return ""
    password = settings.get("server_password", "")
    return password if isinstance(password, str) else ""
