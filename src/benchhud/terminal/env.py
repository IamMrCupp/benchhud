"""The environment a terminal session's shell runs in (every platform)."""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Variables the packaged app sets for its own bundled Python. Leaking them into
#: the operator's shell would break their own ``python``.
_STRIP_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE")


def shell_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for the shell: ours, minus the app's Python, plus a TERM."""
    env = dict(base if base is not None else os.environ)
    for key in _STRIP_ENV:
        env.pop(key, None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    return env
