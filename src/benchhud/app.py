"""Application entry point.

The full HUD shell (QApplication + main window + panel manager) is built out in
Phase 0 §C. For now this is a minimal, importable entry point so packaging and
the ``benchhud`` console script are wired end to end.
"""

from __future__ import annotations

import sys

from benchhud import __version__


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point for ``benchhud``.

    Returns a process exit code. Currently a placeholder that reports the
    version; replaced by the real HUD shell launch in §C.1.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-V", "--version"}:
        print(f"benchhud {__version__}")
        return 0
    print(f"benchhud {__version__} — HUD shell not yet implemented (see plan.md Phase 0 §C)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
