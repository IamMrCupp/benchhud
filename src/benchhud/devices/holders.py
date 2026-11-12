"""Which process holds a camera, so "couldn't open" can say who has it (#325).

A UVC camera allows one reader. When OBS (or a browser tab, or a second
benchhud) opens the thermal camera, the HUD's open fails and the reason was a
generic "check that nothing else has it open". On Linux every open file is a
link under ``/proc/<pid>/fd``, so the holder can be named outright.

Only processes we may look into are seen: our own user's, which is where OBS
runs on the bench. Anything unreadable is skipped, never an error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Holder:
    pid: int
    name: str  # /proc/<pid>/comm, e.g. "obs"


def holders(device: str, proc: str = "/proc", self_pid: int | None = None) -> list[Holder]:
    """The processes (other than this one) with ``device`` open, by pid."""
    if not device.startswith("/"):
        return []  # a camera index or a macOS id: no file to look for
    real = os.path.realpath(device)
    me = os.getpid() if self_pid is None else self_pid
    found = []
    try:
        entries = list(Path(proc).iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue  # another user's process, or it just exited
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target == real:
                found.append(Holder(int(entry.name), _comm(entry)))
                break
    return sorted(found, key=lambda h: h.pid)


def _comm(entry: Path) -> str:
    try:
        return (entry / "comm").read_text().strip() or "?"
    except OSError:
        return "?"


def held_by(device: str, proc: str = "/proc", self_pid: int | None = None) -> str | None:
    """``"held by obs (pid 1234)"`` when another process has it open, else None."""
    found = holders(device, proc, self_pid)
    if not found:
        return None
    return "held by " + ", ".join(f"{h.name} (pid {h.pid})" for h in found)
