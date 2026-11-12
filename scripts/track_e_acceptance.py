#!/usr/bin/env python3
"""Track E acceptance — the live recon-path + stream-safety check, as one command.

Reads the bench-recon (``[recon]``) and bench-parts (``[inventory]``) connections
from the benchhud config and prints a pass/fail report, exiting non-zero if any
check that actually ran failed. Flags override the config so you can point it at a
service without editing config.toml.

    uv run python scripts/track_e_acceptance.py                 # use the live config
    uv run python scripts/track_e_acceptance.py --ref R26-0004  # a specific engagement
    uv run python scripts/track_e_acceptance.py \
        --recon-base http://192.168.7.205:8000 --recon-token "$BENCHRECON_API_TOKEN" \
        --parts-base http://192.168.7.204:8000 --parts-token "$BENCH_PARTS_API_TOKEN"

benchhud is read-only on both backends, so this runs against an existing
engagement and never writes to a system of record. See issue #323.
"""

from __future__ import annotations

import argparse
import sys

from benchhud import config as config_module
from benchhud.acceptance import Report, run

_MARK = {True: "PASS", False: "FAIL"}


def _format(report: Report) -> str:
    lines = []
    for check in report.checks:
        mark = "SKIP" if check.skipped else _MARK[check.ok]
        lines.append(f"[{mark}] {check.name} — {check.detail}")
    ran = len(report.ran)
    passed = ran - len(report.failures)
    skipped = len(report.checks) - ran
    verdict = "PASS" if report.ok else "FAIL"
    lines.append("")
    lines.append(
        f"{verdict}: {passed}/{ran} checks passed" + (f", {skipped} skipped" if skipped else "")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", help="config.toml path (default: the per-OS benchhud config)")
    parser.add_argument("--ref", help="engagement ref to verify (default: the first one listed)")
    parser.add_argument("--recon-base", help="override [recon] base_url")
    parser.add_argument("--recon-token", help="override [recon] token")
    parser.add_argument("--parts-base", help="override [inventory] base_url")
    parser.add_argument("--parts-token", help="override [inventory] token")
    args = parser.parse_args(argv)

    cfg = config_module.load(args.config)
    recon_base = args.recon_base if args.recon_base is not None else cfg.recon.base_url
    recon_token = args.recon_token if args.recon_token is not None else cfg.recon.token
    parts_base = args.parts_base if args.parts_base is not None else cfg.inventory.base_url
    parts_token = args.parts_token if args.parts_token is not None else cfg.inventory.token

    report = run(recon_base, recon_token, parts_base, parts_token, ref=args.ref)
    print(_format(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
