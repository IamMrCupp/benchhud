"""Spot a camera that's come up in its firmware's Recovery Mode (#362).

The Sipeed thermal camera runs its own Linux. Since 2026-11-08 it has started
up as ``Thermal Camera (Recovery Mode)`` on every boot of the bench: the same
USB id, but only a mass-storage disk and no video interface, so there's no
``/dev/video`` node to open. A power cycle of its hub port brings it back as
``Thermal Camera (UVC)``.

The kernel exposes every USB device's product name under ``/sys``, so the mode
can be read without opening anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SYS_USB = "/sys/bus/usb/devices"
RECOVERY = "Recovery Mode"


@dataclass(frozen=True)
class RecoveryDevice:
    name: str  # the kernel's name for it, e.g. "1-4.4"
    product: str  # e.g. "Thermal Camera (Recovery Mode)"

    @property
    def port_spec(self) -> str:
        """Its hub port the way ``reset_port`` and uhubctl write it: ``1-4:4``."""
        return port_spec(self.name)


def port_spec(name: str) -> str:
    """``hub:port`` for a USB device name: ``1-4.4`` → ``1-4:4``, ``1-2`` → ``1:2``."""
    hub, sep, port = name.rpartition(".")
    if not sep:
        hub, _, port = name.rpartition("-")
    return f"{hub}:{port}"


def device_name(spec: str) -> str:
    """The USB device name on a ``hub:port``: ``1-4:4`` → ``1-4.4``, ``1:2`` → ``1-2``."""
    hub, _, port = spec.strip().partition(":")
    return f"{hub}.{port}" if "-" in hub else f"{hub}-{port}"


def recovery_devices(sys_usb: str = SYS_USB) -> list[RecoveryDevice]:
    """Every USB device whose product name says it's in Recovery Mode."""
    try:
        entries = sorted(Path(sys_usb).iterdir())
    except OSError:
        return []
    found = []
    for entry in entries:
        if ":" in entry.name or entry.name.startswith("usb"):
            continue  # an interface, or a root hub
        try:
            product = (entry / "product").read_text().strip()
        except OSError:
            continue
        if RECOVERY in product:
            found.append(RecoveryDevice(entry.name, product))
    return found


def recovery_device(reset_port: str = "", sys_usb: str = SYS_USB) -> RecoveryDevice | None:
    """The device to act on: the one on ``reset_port`` when it's set, else any."""
    found = recovery_devices(sys_usb)
    if reset_port.strip():
        wanted = device_name(reset_port)
        return next((d for d in found if d.name == wanted), None)
    return found[0] if found else None
