"""What one terminal page receives, as a sequence of small event dicts.

The server turns each into a Server-Sent Event; ``None`` means "nothing new,
send a heartbeat". Kept apart from HTTP so the rules are testable directly:

* ``{"t": "hello", "mode": "write"|"share"}`` first, so the page knows whether
  it may type.
* ``{"t": "reset"}`` — clear the screen: a new shell, or output was skipped.
* ``{"t": "size", "cols", "rows"}`` — the pty's size. The share view renders at
  this size and scales to fit, so full-screen programs line up.
* ``{"t": "data", "d": str}`` — output to draw.
* ``{"t": "exit", "code"}`` / ``{"t": "error", "message"}`` — the shell is gone
  or never started.
* Share view only: ``{"t": "off"}`` while sharing is off, ``{"t": "paused"}``
  while paused, ``{"t": "live"}`` when output flows. None of these carry output,
  and the share view is never sent anything from before its floor.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from benchhud.terminal.host import TerminalHost


def terminal_events(
    host: TerminalHost,
    mode: str,
    stopping: Callable[[], bool],
    heartbeat: float = 15.0,
) -> Iterator[dict[str, Any] | None]:
    share = mode == "share"
    yield {"t": "hello", "mode": mode}
    session: int | None = None
    pos = 0
    size: tuple[int, int] | None = None
    exit_sent = False
    error_sent: str | None = None
    view: str | None = None  # share view: "off" | "paused" | "live"

    while not stopping():
        st = host.state()
        events: list[dict[str, Any]] = []

        if share:
            now = "off" if not st.sharing else "paused" if st.paused else "live"
            if now != view:
                if view == "live" or view is None:
                    events.append({"t": "reset"})  # leaving live: nothing stays on screen
                view = now
                events.append({"t": now})
        active = not share or view == "live"

        if st.session != session:
            session, pos, exit_sent, error_sent = st.session, 0, False, None
            if active:
                events.append({"t": "reset"})
        if (st.cols, st.rows) != size:
            size = (st.cols, st.rows)
            events.append({"t": "size", "cols": st.cols, "rows": st.rows})

        if active:
            floor = st.share_floor if share else 0
            new_pos, text, skipped = host.read(pos, floor)
            if skipped and pos > 0:
                events.append({"t": "reset"})  # a gap: don't splice across it
            pos = new_pos
            if text:
                events.append({"t": "data", "d": text})
            if st.error and st.error != error_sent:
                error_sent = st.error
                events.append({"t": "error", "message": st.error})
            if st.exit_code is not None and not exit_sent:
                exit_sent = True
                events.append({"t": "exit", "code": st.exit_code})
        else:
            pos = st.end  # nothing buffered while hidden is ever sent later

        yield from events
        host.wait(pos, st.version, heartbeat)
        if not events and host.state().version == st.version:
            yield None
