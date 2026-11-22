"""Which docks share a tab group, and where each group sits by default (#429).

Fourteen-odd tabs in one left column fit about five titles at its 340 px width.
Splitting them into groups lets every title show: Bench over Recon in the left
column, the tools in a strip under the composite (which is letterboxed, so the
strip costs the picture nothing), the camera views and the meter on the right.

Pure data, so the arrangement is testable without a window; the main window
reads it to build the default layout and each dock's title bar.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Area(Enum):
    """Where a group starts. ``LEFT_LOWER`` is the left column's lower half."""

    LEFT = "left"
    LEFT_LOWER = "left-lower"
    RIGHT = "right"
    RIGHT_LOWER = "right-lower"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class DockGroup:
    """Docks tabbed together under one heading. The first panel opens raised."""

    title: str
    area: Area
    panel_ids: tuple[str, ...]


DOCK_GROUPS: tuple[DockGroup, ...] = (
    DockGroup("Bench", Area.LEFT, ("controls", "cameras", "jobs", "readings", "inventory")),
    DockGroup("Recon", Area.LEFT_LOWER, ("engagement", "interfaces", "pinout", "captures", "chip")),
    DockGroup("Tools", Area.BOTTOM, ("launcher", "terminal", "flash", "minipro")),
    DockGroup("Feeds", Area.RIGHT, ("scope", "thermal")),
    DockGroup("Meter", Area.RIGHT_LOWER, ("power",)),
)


def group_of(panel_id: str) -> DockGroup | None:
    """The group a panel belongs to, or None for a panel outside every group."""
    for group in DOCK_GROUPS:
        if panel_id in group.panel_ids:
            return group
    return None


def heading(title: str) -> str:
    """A group's title as its heading shows it: small capitals, burnwright-style."""
    return title.upper()


#: Bottom strip bounds, in px. Below the floor a terminal is too short to use;
#: above the cap the strip would start eating into a tall composite.
BOTTOM_DOCK_MIN = 180
BOTTOM_DOCK_MAX = 320


def bottom_dock_height(centre_width: int, centre_height: int, aspect: float = 16 / 9) -> int:
    """Default height for the bottom strip: the composite's letterbox, clamped.

    The composite is fitted to the centre at ``aspect``. When the centre is
    taller than that, the spare height is letterbox the strip can take without
    shrinking the picture; it's clamped to a usable range either way.
    """
    picture_height = centre_width / aspect if aspect > 0 else centre_height
    spare = int(centre_height - picture_height)
    return max(BOTTOM_DOCK_MIN, min(BOTTOM_DOCK_MAX, spare))
