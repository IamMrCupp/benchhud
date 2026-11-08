"""Fixed ``/dev/cam-*`` names for the bench cameras, as a udev rules file (#325).

benchhud opens its cameras by their ``/dev/v4l/by-id`` links (#257), so a camera
that drops off USB and comes back under another ``/dev/videoN`` doesn't bother
it. OBS's Video Capture Device sources store ``/dev/videoN`` though. After the
thermal camera reconnects, OBS can open it in place of the webcam it meant, and
since a UVC camera allows one reader, the HUD then can't open the thermal.

``benchhud udev-rules`` prints a rule per plugged-in USB camera that gives it a
short name that never moves: ``/dev/cam-scope`` and ``/dev/cam-thermal`` for the
cameras the config uses, ``/dev/cam-<model>`` for the rest. OBS is pointed at
those names.

Each camera's identity comes from the by-id link udev already made for it,
``usb-<ID_SERIAL>-video-index0``, so the rule matches on exactly what that link
does and needs nothing else installed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from benchhud.devices.discovery import BY_ID_DIR, _read_sysfs_name, same_device

#: The rules file's place. It must sort after systemd's 60-persistent-v4l.rules,
#: which sets the ID_SERIAL the rules match on.
RULES_PATH = "/etc/udev/rules.d/70-benchhud-cameras.rules"

_BY_ID = re.compile(r"^(?P<bus>[a-z0-9]+)-(?P<serial>.+)-video-index(?P<index>\d+)$")


@dataclass(frozen=True)
class CameraId:
    """A camera as udev knows it: what its by-id link was built from."""

    link: str  # /dev/v4l/by-id/usb-...-video-index0
    bus: str  # "usb"
    serial: str  # udev's ID_SERIAL, e.g. Sipeed_Ltd._Thermal_Camera__UVC__0123456789
    label: str  # the camera's own name, for its /dev/cam-* name


@dataclass(frozen=True)
class CameraName:
    camera: CameraId
    name: str  # without the cam- prefix

    @property
    def path(self) -> str:
        return f"/dev/cam-{self.name}"


def parse_by_id(name: str) -> tuple[str, str, int] | None:
    """``(bus, serial, index)`` from a by-id link's file name, or None."""
    match = _BY_ID.match(name)
    if match is None:
        return None
    return match["bus"], match["serial"], int(match["index"])


def slug(text: str) -> str:
    """A camera name fit for ``/dev/cam-<slug>``: lower case, words joined by -."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def usb_cameras(
    by_id_dir: str = BY_ID_DIR,
    label_of: Callable[[str], str | None] = _read_sysfs_name,
) -> list[CameraId]:
    """Every USB camera's capture node, from udev's by-id links.

    Only index 0 counts: a UVC camera's second node carries metadata, not a
    picture (#269). The v4l2loopback output has no by-id link, so it's never
    listed.
    """
    try:
        links = sorted(Path(by_id_dir).iterdir())
    except OSError:
        return []
    cameras = []
    for link in links:
        parsed = parse_by_id(link.name)
        if parsed is None:
            continue
        bus, serial, index = parsed
        if bus != "usb" or index != 0:
            continue
        node = os.path.realpath(link)
        label = label_of(node) or serial
        cameras.append(CameraId(str(link), bus, serial, label))
    return cameras


def name_cameras(
    cameras: Sequence[CameraId],
    roles: Mapping[str, str],
    same: Callable[[str, str], bool] = same_device,
) -> list[CameraName]:
    """Give each camera a unique name.

    ``roles`` maps a role (``scope``, ``thermal``) to the device the config
    opens for it; the camera behind that device takes the role's name. The rest
    are named after their model, with ``-2``, ``-3`` for a repeat.
    """
    named: list[CameraName] = []
    taken: set[str] = set()
    rest: list[CameraId] = []
    for camera in cameras:
        role = next(
            (r for r, device in roles.items() if device.strip() and same(device, camera.link)),
            None,
        )
        if role is not None and role not in taken:
            taken.add(role)
            named.append(CameraName(camera, role))
        else:
            rest.append(camera)
    for camera in rest:
        base = slug(camera.label) or "camera"
        name, n = base, 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
        taken.add(name)
        named.append(CameraName(camera, name))
    return named


def rule_for(named: CameraName) -> str:
    """One udev rule: the camera's capture node gets its /dev/cam-* name."""
    camera = named.camera
    return (
        f'SUBSYSTEM=="video4linux", ENV{{ID_BUS}}=="{camera.bus}", '
        f'ENV{{ID_SERIAL}}=="{camera.serial}", ATTR{{index}}=="0", '
        f'SYMLINK+="cam-{named.name}"'
    )


def rules_file(named: Sequence[CameraName]) -> str:
    """The whole rules file, with a note on where it goes and what it's for."""
    lines = [
        "# benchhud: fixed names for the bench cameras (#325).",
        "# A camera that drops off USB can come back as another /dev/videoN; these",
        "# names follow the camera. Point OBS's Video Capture Device sources at them.",
        f"# Install: benchhud udev-rules | sudo tee {RULES_PATH}",
        "#          sudo udevadm control --reload && sudo udevadm trigger",
        "",
    ]
    for item in named:
        lines.append(f"# {item.path}: {item.camera.label}")
        lines.append(rule_for(item))
    return "\n".join(lines) + "\n"


def udev_rules_main(
    out: Callable[[str], None] | None = None,
    err: Callable[[str], None] | None = None,
    platform: str | None = None,
    cameras: Callable[[], list[CameraId]] = usb_cameras,
    roles: Callable[[], Mapping[str, str]] | None = None,
) -> int:
    """``benchhud udev-rules``: print the rules file for the cameras plugged in."""
    import sys

    out = out or print
    err = err or (lambda text: print(text, file=sys.stderr))
    if not (platform or sys.platform).startswith("linux"):
        err("benchhud udev-rules: udev is Linux only")
        return 1
    if roles is None:

        def roles() -> Mapping[str, str]:
            from benchhud import config as config_module

            panels = config_module.load().panels
            return {"scope": panels.scope.device, "thermal": panels.thermal.device}

    found = cameras()
    if not found:
        err(f"benchhud udev-rules: no USB cameras under {BY_ID_DIR}")
        return 1
    out(rules_file(name_cameras(found, roles())).rstrip("\n"))
    return 0
