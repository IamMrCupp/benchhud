"""An in-app terminal, drawn by xterm.js, shareable to OBS read-only (#194).

* :mod:`~benchhud.terminal.pty_session` — a shell in a POSIX pseudo-terminal.
* :mod:`~benchhud.terminal.host` — the scrollback, size, exit and share state
  the pages read, plus the two access tokens.

The page itself is ``resources/web/terminal.html``, served by
:mod:`benchhud.browser_sources.server`: inside the app through a Qt web view
(interactive), and to OBS as a Browser source (output only).
"""

from __future__ import annotations

import os
import sys

from benchhud.terminal.host import TerminalHost, TerminalState

#: Platforms with a pseudo-terminal backend. Windows needs ConPTY (a follow-up).
SUPPORTED = sys.platform != "win32"


def login_shell(configured: str = "") -> list[str]:
    """argv for the shell: the configured one, else the user's login shell, as a login shell."""
    shell = configured.strip()
    if not shell:
        shell = os.environ.get("SHELL", "")
    if not shell and SUPPORTED:
        import pwd  # POSIX only

        try:
            shell = pwd.getpwuid(os.getuid()).pw_shell
        except KeyError:
            shell = ""
    return [shell or "/bin/sh", "-l"]


__all__ = ["SUPPORTED", "TerminalHost", "TerminalState", "login_shell"]
