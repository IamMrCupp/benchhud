"""``benchhud job``: list, create and select jobs on the running app (#433).

Selecting a job used to be a click in the Jobs dock and nothing else, which is
no help from an SSH session, a script or a Stream Deck button::

    benchhud job list
    benchhud job new MacBook Pro A1708 --board-id 820-00840
    benchhud job select 12
    benchhud job set 12 --status waiting

These talk to the benchhud that's already running, over its loopback server, so
the Jobs dock and the on-stream job card follow at once. Nothing here opens
``jobs.db``: when the app isn't running the command says so and exits non-zero.

The port and the token come from ``control.json`` beside the config, which the
app writes owner-only at start (see :mod:`benchhud.browser_sources.control`).
Replies carry the job card's fields only — never the customer, serial or
symptoms — so the output is safe in a terminal that's on stream. Nothing here
imports Qt, so it runs with no display.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchhud.browser_sources.control import (
    FIELD_LIMITS,
    STATUSES,
    default_control_path,
    read_control_file,
)

#: Exit code when the command failed, including "benchhud isn't running".
EXIT_FAILED = 1

HOST = "127.0.0.1"
TIMEOUT_S = 10.0

NOT_RUNNING = (
    "benchhud isn't running, or its local server is off "
    "(Preferences → Output → Serve panels to OBS as Browser sources)"
)


class JobCommandError(Exception):
    """The command didn't happen; the message says why."""


def _call(control_file: Path, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    """One request to the running app. Raises :class:`JobCommandError` on any failure."""
    found = read_control_file(control_file)
    if found is None:
        raise JobCommandError(NOT_RUNNING)
    port, token = found
    headers = {"Authorization": f"Bearer {token}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection(HOST, port, timeout=TIMEOUT_S)
    try:
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
    except (OSError, http.client.HTTPException):
        # A file left by a crash, with nothing listening behind it.
        raise JobCommandError(NOT_RUNNING) from None
    finally:
        conn.close()
    try:
        reply = json.loads(raw)
    except ValueError:
        reply = None
    if response.status == http.client.FORBIDDEN:
        # Something answers on that port, but not the benchhud that wrote the file.
        raise JobCommandError(NOT_RUNNING)
    if response.status != http.client.OK or not isinstance(reply, dict):
        detail = reply.get("error") if isinstance(reply, dict) else None
        raise JobCommandError(detail or f"benchhud answered {response.status}")
    return reply


def _line(job: dict[str, Any], selected: object) -> str:
    """``* #12  MacBook Pro — open  820-00239  26-0001``; the star is the selected job."""
    mark = "*" if job.get("id") == selected else " "
    text = f"{mark} #{job.get('id')}  {job.get('device')} — {job.get('status')}"
    for extra in (job.get("board_id"), job.get("external_ref")):
        if extra:
            text += f"  {extra}"
    return text


def _field_flags(*, device: bool) -> argparse.ArgumentParser:
    """``--board-id``, ``--serial`` and the rest (#436), shared by ``new`` and ``set``.

    A flag that isn't given isn't sent, so ``set`` changes only what's named.
    An empty value (``--serial ""``) clears the field.
    """
    flags = argparse.ArgumentParser(add_help=False)
    if device:
        flags.add_argument("--device", help="the board or unit")
    flags.add_argument("--board-id", help="the board's part number, e.g. 820-00239")
    flags.add_argument("--serial", help="serial number")
    flags.add_argument("--customer", help="who it belongs to (never shown back, or on stream)")
    flags.add_argument("--symptoms", help="what it does wrong")
    flags.add_argument("--status", choices=STATUSES, help="one of: " + ", ".join(STATUSES))
    return flags


def _fields(args: argparse.Namespace) -> dict[str, str]:
    """The field flags that were given, by the name the app knows them by."""
    given = {name: getattr(args, name, None) for name in FIELD_LIMITS}
    return {name: value for name, value in given.items() if isinstance(value, str)}


def main(argv: Sequence[str], control_file: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchhud job",
        description="List, create and select jobs on the benchhud that's running.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    as_json = argparse.ArgumentParser(add_help=False)
    as_json.add_argument("--json", action="store_true", help="print the reply as JSON")
    sub.add_parser("list", parents=[as_json], help="the jobs, newest first; * is the selected one")
    new_p = sub.add_parser(
        "new",
        parents=[as_json, _field_flags(device=False)],
        help="create a job and select it",
        description="Create a job and select it, as the New job button does. "
        "The flags fill in the rest of the job at the same time.",
    )
    new_p.add_argument("device", nargs="+", help="the board or unit; no quotes needed")
    select_p = sub.add_parser(
        "select",
        parents=[as_json],
        help="select a job by id",
        description="Select a job, as clicking its row does. The job card follows.",
    )
    select_p.add_argument("id", type=int, help="the job's number, from `benchhud job list`")
    set_p = sub.add_parser(
        "set",
        parents=[as_json, _field_flags(device=True)],
        help="change a job's fields",
        description="Change fields on a job, as editing the detail form and hitting Save does. "
        "Only the flags you give are changed. The selection stays where it is.",
    )
    set_p.add_argument("id", type=int, help="the job's number, from `benchhud job list`")

    args = parser.parse_args(list(argv))
    if args.command == "set" and not _fields(args):
        set_p.error("nothing to set: give at least one of --device, --board-id, …")
    path = control_file or default_control_path()
    try:
        if args.command == "list":
            reply = _call(path, "GET", "/control/jobs")
        elif args.command == "new":
            fields = {**_fields(args), "device": " ".join(args.device)}
            reply = _call(path, "POST", "/control/jobs", fields)
        elif args.command == "set":
            reply = _call(path, "POST", "/control/jobs/set", {"id": args.id, **_fields(args)})
        else:
            reply = _call(path, "POST", "/control/jobs/select", {"id": args.id})
    except JobCommandError as exc:
        print(f"benchhud job: {exc}", file=sys.stderr)
        return EXIT_FAILED

    if args.json:
        print(json.dumps(reply))
    elif args.command == "list":
        jobs = reply.get("jobs") or []
        for job in jobs:
            print(_line(job, reply.get("selected")))
        if not jobs:
            print("no jobs yet")
    else:
        print(_line(reply.get("job") or {}, reply.get("selected")))
    return 0
