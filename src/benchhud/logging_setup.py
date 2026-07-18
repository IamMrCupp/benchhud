"""Logging configuration for the app."""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging from a level name (case-insensitive).

    Unknown level names fall back to INFO rather than raising.
    """
    resolved = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(level=resolved, format=_FORMAT)
