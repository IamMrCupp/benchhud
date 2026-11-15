"""Why the v4l2loopback node isn't there, in words the operator can act on (#240).

"No such file or directory" for ``/dev/video10`` covers several different
problems, each with its own fix:

* the ``v4l2loopback`` module isn't loaded at all;
* it's loaded, but with other options. OBS loads it itself on Start Virtual
  Camera, as ``/dev/video5`` "OBS Virtual Camera" or so, and module options in
  ``/etc/modprobe.d`` only apply when the module loads, so ``video_nr=10``
  never happened;
* the configured path is a real camera, not a loopback node;
* it loaded from the initramfs with an older copy of the options, so a node
  ``/etc/modprobe.d`` asks for was never created (#375).

Everything comes from ``/sys`` and ``/etc/modprobe.d``. Both roots are
injectable, so tests build a fake tree instead of needing the module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The command BENCH-SETUP gives, for when the module isn't loaded.
MODPROBE = "sudo modprobe v4l2loopback exclusive_caps=1 card_label=benchhud video_nr=10"

#: Where the module's options live on disk. A name, so tests can point it away.
MODPROBE_DIR = "/etc/modprobe.d"


@dataclass(frozen=True)
class LoopbackNode:
    path: str  # /dev/videoN
    label: str  # the card_label it was loaded with

    def __str__(self) -> str:
        return f"{self.path} ({self.label})" if self.label else self.path


def module_loaded(sys_root: str | Path = "/sys") -> bool:
    return (Path(sys_root) / "module" / "v4l2loopback").is_dir()


def loopback_nodes(sys_root: str | Path = "/sys") -> list[LoopbackNode]:
    """The v4l2loopback nodes that exist. They're virtual devices, not USB ones."""
    base = Path(sys_root) / "devices" / "virtual" / "video4linux"
    if not base.is_dir():
        return []
    nodes = []
    for entry in sorted(base.glob("video*"), key=lambda p: int(p.name[5:] or -1)):
        try:
            label = (entry / "name").read_text().strip()
        except OSError:
            label = ""
        nodes.append(LoopbackNode(f"/dev/{entry.name}", label))
    return nodes


def configured_video_nrs(modprobe_dir: str | Path | None = None) -> list[int]:
    """The ``video_nr`` list ``/etc/modprobe.d`` asks v4l2loopback for, or [].

    The last ``options v4l2loopback`` line with a ``video_nr=`` wins, files in
    name order.
    """
    numbers: list[int] = []
    base = Path(MODPROBE_DIR if modprobe_dir is None else modprobe_dir)
    if not base.is_dir():
        return numbers
    for conf in sorted(base.glob("*.conf")):
        try:
            lines = conf.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            words = line.split("#", 1)[0].split()
            if words[:2] != ["options", "v4l2loopback"]:
                continue
            for word in words[2:]:
                match = re.fullmatch(r"video_nr=([\d,]+)", word)
                if match:
                    numbers = [int(n) for n in match.group(1).split(",") if n]
    return numbers


def diagnose(
    device: str,
    sys_root: str | Path = "/sys",
    modprobe_dir: str | Path | None = None,
) -> str | None:
    """Why ``device`` can't be the loopback output, or None if it looks right.

    None means the node exists and is a loopback node, so the failure was
    something else (another writer holding it, a format the driver refused),
    and the original error says more.
    """
    if not module_loaded(sys_root):
        return f"no {device}: v4l2loopback isn't loaded. Load it with: {MODPROBE}"
    nodes = loopback_nodes(sys_root)
    if any(node.path == device for node in nodes):
        return None
    sysfs_name = Path(sys_root) / "class" / "video4linux" / Path(device).name
    problem = (
        f"{device} is a camera, not a v4l2loopback node."
        if sysfs_name.exists()
        else f"{device} doesn't exist."
    )
    if not nodes:
        return f"{problem} v4l2loopback is loaded but has no nodes; reload it: {MODPROBE}"
    listing = ", ".join(str(node) for node in nodes)
    number = re.fullmatch(r"/dev/video(\d+)", device)
    wanted = configured_video_nrs(modprobe_dir)
    have = {int(node.path[10:]) for node in nodes}
    asked_for = bool(number) and int(number.group(1)) in wanted
    if not sysfs_name.exists() and asked_for and have <= set(wanted):
        # /etc/modprobe.d asks for this node, and every node here is one it asks
        # for too, so the module did read our options, just an older copy: the
        # one in the initramfs (#375). Nodes outside the list mean something
        # else (OBS) loaded it with its own options, which the hint below covers.
        return (
            f"{problem} v4l2loopback loaded with {len(nodes)} of the {len(wanted)} nodes "
            f"/etc/modprobe.d asks for ({listing}), so it read older options, most "
            "likely the initramfs copy. Run: sudo update-initramfs -u, then quit OBS "
            "and reload it: sudo modprobe -r v4l2loopback && sudo modprobe v4l2loopback "
            "(BENCH-SETUP: Rebuild the initramfs after changing the options)."
        )
    hint = f" Loopback nodes here: {listing}. Pick one in Preferences → Output"
    if all("obs" in node.label.lower() for node in nodes):
        # OBS loaded the module with its own options, so ours never applied.
        hint += (
            ", or give benchhud a node of its own (BENCH-SETUP: two loopback devices), "
            "since OBS's Start Virtual Camera writes to that one"
        )
    return f"{problem}{hint}."
