"""Job control on the running app (#433): the token, its file, and the GUI hooks.

``benchhud job …`` has to reach a HUD that's already running, from an SSH
session or a script. The loopback server takes the commands; this module holds
what the two ends share:

* :class:`JobControl` — what the server calls. The three hooks are supplied by
  the shell and each one blocks until the GUI thread has done the work.
* the **control file** — ``control.json`` beside the config, owner-only
  (``0600``), written when the server starts and removed when it stops. It
  holds the server's port and a token that's new every launch. Reading that
  file is how the CLI proves it's the same user on the same machine.

The token travels in an ``Authorization`` header, never in the URL, so it stays
out of the request line the server logs. Stdlib only, and nothing here imports
Qt: the CLI loads this module with no display.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FILE_NAME = "control.json"

#: Longest device name ``job new`` accepts. A name, not a paste.
MAX_DEVICE_CHARS = 200

#: The job fields ``job new`` and ``job set`` may write (#436), and the longest
#: value each takes. What the detail form saves, plus the board number.
FIELD_LIMITS: dict[str, int] = {
    "device": MAX_DEVICE_CHARS,
    "board_id": 64,
    "serial": 128,
    "customer": 200,
    "symptoms": 4000,
    "status": 32,
}

#: The statuses a job can have: the store's ``JobStatus``, spelled out here so
#: the CLI doesn't import the store. A test holds the two together.
STATUSES = ("open", "in_progress", "waiting", "done", "scrapped")


def clean_fields(raw: dict[str, Any]) -> dict[str, str]:
    """Check the fields of a ``job new`` or ``job set`` request; return them stripped.

    Raises ``ValueError`` naming the problem: a field that can't be set, a value
    that isn't text or is too long, an empty device, a status the store doesn't
    know. Every other field may be set to nothing, which clears it. The message
    names the field and never repeats the value — it may be a customer's name.
    """
    unknown = sorted(set(raw) - set(FIELD_LIMITS))
    if unknown:
        raise ValueError(
            f"can't set {', '.join(unknown)}; the fields are {', '.join(FIELD_LIMITS)}"
        )
    fields: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        value = value.strip()
        if len(value) > FIELD_LIMITS[name]:
            raise ValueError(f"{name} is over {FIELD_LIMITS[name]} characters")
        fields[name] = value
    if "device" in fields and not fields["device"]:
        raise ValueError("a job needs a device name")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    return fields


def default_control_path() -> Path:
    """``control.json`` beside the live config file."""
    from benchhud.config import default_config_path

    return default_config_path().parent / FILE_NAME


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class JobControl:
    """The job commands the server may run, and the token that allows them.

    Each hook returns a JSON-ready dict built from the job-card whitelist
    (:mod:`~benchhud.browser_sources.payloads`). ``create_job`` and
    ``update_job`` take fields already through :func:`clean_fields`;
    ``select_job`` and ``update_job`` return None for an id that doesn't exist;
    any of them raises ``TimeoutError`` when the GUI thread didn't answer.
    """

    list_jobs: Callable[[], dict[str, Any]]
    create_job: Callable[[dict[str, str]], dict[str, Any]]
    select_job: Callable[[int], dict[str, Any] | None]
    update_job: Callable[[int, dict[str, str]], dict[str, Any] | None]
    token: str = field(default_factory=new_token, repr=False)

    def authorised(self, header: str | None) -> bool:
        """Whether an ``Authorization`` header carries this run's token."""
        scheme, _, given = (header or "").partition(" ")
        if scheme.lower() != "bearer" or not given:
            return False
        return secrets.compare_digest(given.strip().encode(), self.token.encode())


def write_control_file(path: Path, port: int, token: str) -> None:
    """Write ``path`` owner-only, replacing whatever a previous run left."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()  # so the new file gets this mode, whatever the old one had
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"port": port, "token": token}, handle)


def read_control_file(path: Path) -> tuple[int, str] | None:
    """``(port, token)`` from ``path``, or None when it's missing or unreadable."""
    try:
        data = json.loads(path.read_text("utf-8"))
        port, token = int(data["port"]), str(data["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (port, token) if token else None


def remove_control_file(path: Path, token: str) -> None:
    """Remove ``path`` if it's this run's — a second benchhud's file is left alone."""
    current = read_control_file(path)
    if current is not None and secrets.compare_digest(current[1].encode(), token.encode()):
        with contextlib.suppress(OSError):
            path.unlink()
