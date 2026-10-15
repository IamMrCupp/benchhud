"""minipro, driven as a subprocess, for the XGecu T48 / TL866 (#253).

The Flash panel's twin for socketed and clipped chips on a T48: EEPROMs,
SPI flash, parallel flash. minipro can't detect a chip by itself (it needs the
part name), so the flow is: search the name, check the chip's ID matches, read
it twice as a backup, write with minipro's own verify.

Like flashrom, it runs as a separate program, never linked; output is parsed
only for the chip list, its size, and the ID check. minipro prints all of it on
stderr, which the runner merges.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from benchhud.flash.flashrom import DumpResult, FlashError, LineSink, Runner, read_twice

_MEMORY = re.compile(r"^(?:Code )?[Mm]emory:\s*(\d+)\s*(?:Bytes|bytes)", re.MULTILINE)
_MISMATCH = re.compile(r"Chip ID mismatch: expected (0x[0-9A-Fa-f]+), got (0x[0-9A-Fa-f]+)")
_NOISE = ("Found T", "Warning:", "Expected", "Found  ", "Device code", "Serial code")


def _collect(runner: Runner, argv: list[str], on_line: LineSink | None = None) -> tuple[int, str]:
    lines: list[str] = []

    def sink(line: str) -> None:
        lines.append(line)
        if on_line is not None:
            on_line(line)

    return runner.run(argv, sink), "\n".join(lines)


def search(runner: Runner, command: str, query: str, limit: int = 200) -> list[str]:
    """Chip names matching ``query`` (minipro's own search), e.g. W25Q64BV@SOIC8."""
    query = query.strip()
    if len(query) < 2:
        return []
    _, output = _collect(runner, [command, "-L", query])
    names = [
        line.strip()
        for line in output.splitlines()
        if line.strip() and not line.startswith(_NOISE) and " " not in line.strip()
    ]
    return names[:limit]


@dataclass(frozen=True)
class ChipInfo:
    name: str
    size: int  # bytes of code memory


def chip_info(runner: Runner, command: str, name: str) -> ChipInfo:
    code, output = _collect(runner, [command, "-d", name])
    found = _MEMORY.search(output)
    if code != 0 or found is None:
        raise FlashError(f"minipro doesn't know the chip {name!r}")
    return ChipInfo(name, int(found.group(1)))


@dataclass(frozen=True)
class IdCheck:
    ok: bool
    expected: str | None = None
    got: str | None = None

    def describe(self, name: str) -> str:
        if self.ok:
            return f"The chip answers as a {name}."
        if self.got in ("0x0000", "0x000000", "0xFFFF", "0xFFFFFF", "0xFFFFFFFF"):
            return (
                f"No chip answered (got {self.got}). Check it's seated, pin 1 is at "
                "the lever end, and the package matches the name."
            )
        return (
            f"The chip's ID is {self.got}, but a {name} should be {self.expected}. "
            "Wrong part picked, or a different chip in the socket."
        )


def check_id(runner: Runner, command: str, name: str, on_line: LineSink) -> IdCheck:
    """Read the chip's ID and compare it with what ``name`` should be."""
    code, output = _collect(runner, [command, "-p", name, "-D"], on_line)
    mismatch = _MISMATCH.search(output)
    if mismatch:
        return IdCheck(False, mismatch.group(1), mismatch.group(2))
    if code != 0:
        raise FlashError(f"minipro couldn't read the chip's ID (exit {code})")
    return IdCheck(True)


def dump_name(name: str, stamp: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "chip"
    return f"{safe}-{stamp}.bin"


def read_verified(
    runner: Runner, command: str, name: str, dest: Path, on_line: LineSink
) -> DumpResult:
    """Read twice; only a matching pair becomes ``dest``."""
    return read_twice(
        runner, lambda path: [command, "-p", name, "-r", str(path)], dest, on_line, "minipro"
    )


def check_image(info: ChipInfo, image: Path) -> None:
    size = image.stat().st_size
    if size != info.size:
        raise FlashError(
            f"{image.name} is {size:,} bytes but the {info.name} holds {info.size:,}. "
            "Wrong image, or wrong chip."
        )


def write_image(
    runner: Runner,
    command: str,
    info: ChipInfo,
    image: Path,
    on_line: LineSink,
    sha256: Callable[[Path], str],
) -> str:
    """Write ``image``; minipro verifies after writing unless told not to."""
    check_image(info, image)
    code = runner.run([command, "-p", info.name, "-w", str(image)], on_line)
    if code != 0:
        raise FlashError(f"the write failed (exit {code}); the chip may be half-written")
    return sha256(image)


def erase_chip(runner: Runner, command: str, name: str, on_line: LineSink) -> None:
    code = runner.run([command, "-p", name, "-E"], on_line)
    if code != 0:
        raise FlashError(f"the erase failed (exit {code})")
