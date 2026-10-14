"""flashrom, driven as a subprocess (#227).

Reading a BIOS chip is the same steps every time, and on the command line the
dangerous one is easy to miss: a clip that slipped reads garbage without a
complaint. So a backup here is **two reads that must match**, and a write is
refused unless the image is exactly the chip's size. flashrom verifies a write
itself, and its exit code is the verdict.

flashrom runs as a separate program, never linked, so any installed version
works and nothing GPL is linked into benchhud. Its output is parsed only for
what it detected; everything else is shown as-is.

No Qt here: the runner is injectable, so tests never start a process.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

#: Programmers offered in the panel, as (label, ``-p`` value). The combo is
#: editable for anything else flashrom supports.
PROGRAMMERS = (
    ("CH341A (USB)", "ch341a_spi"),
    ("Raspberry Pi SPI", "linux_spi:dev=/dev/spidev0.0,spispeed=8000"),
    ("FT2232H / Tigard", "ft2232_spi:type=2232H"),
    ("serprog (Pico, Arduino)", "serprog:dev=/dev/ttyACM0"),
    ("Dediprog", "dediprog"),
)

#: One line of flashrom output.
LineSink = Callable[[str], None]

_FOUND = re.compile(
    r'Found (?P<vendor>.+?) flash chip "(?P<name>[^"]+)" \((?P<kb>\d+) kB, (?P<bus>[^)]+)\)'
)


class FlashError(Exception):
    """An operation refused before it ran, or flashrom failed."""


class Runner(Protocol):
    def run(self, argv: list[str], on_line: LineSink) -> int: ...

    def cancel(self) -> None: ...


@dataclass(frozen=True)
class Chip:
    vendor: str
    name: str  # flashrom's own name, which is what ``-c`` takes
    size_kb: int
    bus: str = "SPI"

    @property
    def size_bytes(self) -> int:
        return self.size_kb * 1024

    def __str__(self) -> str:
        return f"{self.vendor} {self.name} ({self.size_kb} kB)"


@dataclass(frozen=True)
class DumpResult:
    """A backup: the file and its hash when both reads matched."""

    matched: bool
    path: Path | None  # the dump, when the reads matched
    sha256: str  # of the first read
    reads: tuple[Path, Path] | None = None  # both reads, kept when they differ


class ProcessRunner:
    """Runs flashrom, streaming its output line by line. ``cancel`` stops it."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def run(self, argv: list[str], on_line: LineSink) -> int:
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                errors="replace",
            )
        except OSError as exc:
            raise FlashError(f"couldn't run {argv[0]}: {exc}") from exc
        with self._lock:
            self._proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line.rstrip())
        code = proc.wait()
        with self._lock:
            self._proc = None
        return code

    def cancel(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()


def parse_chips(output: str) -> list[Chip]:
    """The chips flashrom said it found. More than one means pick with ``-c``."""
    return [
        Chip(m["vendor"], m["name"], int(m["kb"]), m["bus"].strip())
        for m in _FOUND.finditer(output)
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dump_name(chip: Chip, stamp: str) -> str:
    """``W25Q64FV-20261013-191500.bin``: flashrom names can hold ``/``."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", chip.name).strip("_") or "chip"
    return f"{safe}-{stamp}.bin"


def _base(command: str, programmer: str, chip: Chip | None) -> list[str]:
    argv = [command, "-p", programmer]
    if chip is not None:
        argv += ["-c", chip.name]
    return argv


def probe(runner: Runner, command: str, programmer: str, on_line: LineSink) -> list[Chip]:
    """Detect the chip. An empty list means nothing answered (check the clip)."""
    lines: list[str] = []

    def sink(line: str) -> None:
        lines.append(line)
        on_line(line)

    runner.run(_base(command, programmer, None), sink)  # its exit code means little here
    return parse_chips("\n".join(lines))


def read_verified(
    runner: Runner,
    command: str,
    programmer: str,
    chip: Chip,
    dest: Path,
    on_line: LineSink,
) -> DumpResult:
    """Read the chip twice. Only a matching pair becomes ``dest``.

    A mismatch keeps both reads beside ``dest``, for a look, and returns
    ``matched=False``: the usual cause is a clip that isn't seated.
    """
    first = dest.with_name(dest.stem + ".read1.bin")
    second = dest.with_name(dest.stem + ".read2.bin")
    for path in (first, second):
        code = runner.run([*_base(command, programmer, chip), "-r", str(path)], on_line)
        if code != 0:
            raise FlashError(f"flashrom couldn't read the chip (exit {code})")
    hashes = sha256_file(first), sha256_file(second)
    if hashes[0] != hashes[1]:
        return DumpResult(matched=False, path=None, sha256=hashes[0], reads=(first, second))
    first.replace(dest)
    second.unlink()
    dest.with_name(dest.name + ".sha256").write_text(f"{hashes[0]}  {dest.name}\n")
    return DumpResult(matched=True, path=dest, sha256=hashes[0])


def check_image(chip: Chip, image: Path) -> None:
    """Refuse an image that isn't exactly the chip's size."""
    size = image.stat().st_size
    if size != chip.size_bytes:
        raise FlashError(
            f"{image.name} is {size:,} bytes but the {chip.name} holds "
            f"{chip.size_bytes:,}. Wrong image, or wrong chip."
        )


def write_image(
    runner: Runner,
    command: str,
    programmer: str,
    chip: Chip,
    image: Path,
    on_line: LineSink,
) -> str:
    """Write ``image`` and let flashrom verify it. Returns the image's sha256."""
    check_image(chip, image)
    code = runner.run([*_base(command, programmer, chip), "-w", str(image)], on_line)
    if code != 0:
        raise FlashError(f"the write failed (exit {code}); the chip may be half-written")
    return sha256_file(image)


def erase_chip(
    runner: Runner, command: str, programmer: str, chip: Chip, on_line: LineSink
) -> None:
    code = runner.run([*_base(command, programmer, chip), "-E"], on_line)
    if code != 0:
        raise FlashError(f"the erase failed (exit {code})")
