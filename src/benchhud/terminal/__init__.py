"""An in-app terminal, drawn by xterm.js, shareable to OBS read-only (#194).

* :mod:`~benchhud.terminal.pty_session` — a shell in a POSIX pseudo-terminal
  (macOS, Linux).
* :mod:`~benchhud.terminal.conpty_session` — the same on Windows, in a ConPTY
  pseudo-console (#203).
* :mod:`~benchhud.terminal.host` — the scrollback, size, exit and share state
  the pages read, plus the two access tokens. It picks the session for the OS.

The page itself is ``resources/web/terminal.html``, served by
:mod:`benchhud.browser_sources.server`: inside the app through a Qt web view
(interactive), and to OBS as a Browser source (output only).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from benchhud.terminal.host import TerminalHost, TerminalState

#: Platforms with a pseudo-terminal backend: a POSIX pty, or ConPTY on Windows.
SUPPORTED = sys.platform == "win32" or os.name == "posix"


def login_shell(
    configured: str = "",
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """argv for the shell: the configured one, else the user's own.

    On macOS and Linux that's ``$SHELL`` (then the passwd entry), run as a login
    shell. On Windows it's ``%COMSPEC%``, the system's command interpreter, then
    PowerShell. Windows has no login-shell flag, and a configured path may hold
    spaces (``C:\\Program Files\\PowerShell\\7\\pwsh.exe``), so there the whole
    setting is the program and nothing is appended.
    """
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    shell = configured.strip()
    if platform == "win32":
        return [shell or env.get("COMSPEC", "").strip() or "powershell.exe"]
    if not shell:
        shell = env.get("SHELL", "")
    if not shell and platform == sys.platform:
        import pwd  # POSIX only

        try:
            shell = pwd.getpwuid(os.getuid()).pw_shell
        except KeyError:
            shell = ""
    return [shell or "/bin/sh", "-l"]


__all__ = ["SUPPORTED", "TerminalHost", "TerminalState", "login_shell"]
