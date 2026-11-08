"""``benchhud config``: set config values from a script, without the GUI.

``bench-setup`` (the bench-suite installer) uses this to point benchhud at
intake, bench-parts and bench-recon::

    benchhud config set intake.base_url http://127.0.0.1:8401
    printf '%s' "$TOKEN" | benchhud config set intake.token -
    benchhud config path

A value of ``-`` is read from stdin, so a token never appears in ``ps`` or shell
history. Only scalar fields of the config model can be set, and the value must
parse as the field's type. Nothing here imports Qt, so it runs with no display.

benchhud saves its config when it closes, so quit it before running ``set`` or
the running app writes its own copy over the change.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from typing import Any, TextIO

from benchhud import config as config_module

#: Exit code for a bad key or value (argparse also uses 2 for usage errors).
EXIT_BAD_INPUT = 2

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


class ConfigKeyError(ValueError):
    """The key doesn't name a settable field, or the value doesn't fit it."""


def _resolve(cfg: config_module.Config, key: str) -> tuple[Any, str, Any]:
    """Walk ``section.sub.field`` to ``(owner, field_name, current_value)``."""
    parts = key.split(".")
    if len(parts) < 2 or not all(parts):
        raise ConfigKeyError(f"{key!r}: use section.field, e.g. intake.base_url")
    owner: Any = cfg
    for part in parts[:-1]:
        if not dataclasses.is_dataclass(owner) or part not in _field_names(owner):
            raise ConfigKeyError(f"{key!r}: no such config section")
        owner = getattr(owner, part)
    name = parts[-1]
    if not dataclasses.is_dataclass(owner) or name not in _field_names(owner):
        raise ConfigKeyError(f"{key!r}: no such config field")
    current = getattr(owner, name)
    if dataclasses.is_dataclass(current):
        raise ConfigKeyError(f"{key!r} is a section; set one of its fields")
    if not isinstance(current, (str, bool, int, float)):
        raise ConfigKeyError(f"{key!r} isn't a plain value and can't be set from here")
    return owner, name, current


def _field_names(obj: Any) -> set[str]:
    return {f.name for f in dataclasses.fields(obj)}


def _coerce(raw: str, current: Any, key: str) -> Any:
    """Parse ``raw`` as the type of the field's current value."""
    if isinstance(current, bool):  # before int: bool is an int subclass
        lowered = raw.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ConfigKeyError(f"{key!r} takes true or false, not {raw!r}")
    if isinstance(current, int):
        try:
            return int(raw.strip())
        except ValueError:
            raise ConfigKeyError(f"{key!r} takes a whole number, not {raw!r}") from None
    if isinstance(current, float):
        try:
            return float(raw.strip())
        except ValueError:
            raise ConfigKeyError(f"{key!r} takes a number, not {raw!r}") from None
    return raw


def _read_stdin(stream: TextIO) -> str:
    """The whole of stdin, minus one trailing newline (what ``echo`` adds)."""
    value = stream.read()
    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n"):
        return value[:-1]
    return value


def set_value(key: str, raw: str, path: str | None = None) -> str:
    """Set ``key`` to ``raw`` in the config at ``path`` (or the default), and save.

    Returns the path written. Raises :class:`ConfigKeyError` for a bad key or value.
    """
    cfg = config_module.load(path)
    owner, name, current = _resolve(cfg, key)
    setattr(owner, name, _coerce(raw, current, key))
    return str(config_module.save(cfg, path))


def main(argv: Sequence[str], stdin: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchhud config",
        description="Read or change benchhud's config without opening the app.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    set_p = sub.add_parser(
        "set",
        help="set one value (quit benchhud first)",
        description="Set one config value. Use - as the value to read it from stdin.",
    )
    set_p.add_argument("key", help="section.field, e.g. inventory.base_url")
    set_p.add_argument("value", help="the new value, or - to read it from stdin")
    sub.add_parser("path", help="print the config file's location")

    args = parser.parse_args(list(argv))
    if args.command == "path":
        print(config_module.default_config_path())
        return 0

    raw = _read_stdin(stdin or sys.stdin) if args.value == "-" else args.value
    try:
        written = set_value(args.key, raw)
    except ConfigKeyError as exc:
        print(f"benchhud config: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    # Never echo the value: it may be a token.
    print(f"set {args.key} in {written}")
    return 0
