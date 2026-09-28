"""config.example.toml stays honest.

The [inventory] block was forgotten when the inventory source landed (#168) —
the example is the only setup doc most users read, so a missing section is a
silently undiscoverable feature. This pins every Config section to the file.
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from benchhud.config import Config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"


def test_example_parses_into_config() -> None:
    data = tomllib.loads(EXAMPLE.read_text())

    Config.from_dict(data)  # raises if the example drifts from the schema


def test_every_config_section_appears_in_the_example() -> None:
    data = tomllib.loads(EXAMPLE.read_text())

    sections = {f.name for f in dataclasses.fields(Config)}
    missing = sections - set(data)
    assert not missing, f"config.example.toml is missing sections: {sorted(missing)}"
