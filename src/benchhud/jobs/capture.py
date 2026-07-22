"""Capture the current bench state into a job's timeline.

This is the thing a standalone ticket app can't do: benchhud already holds the
cameras and the instruments, so one action can append *what you were looking at
and measuring* — scope frame, thermal frame, live V/A/W and DMM — to a repair
job as an image plus a summary note.

Composition is pure (arrays in, array out) so it's tested with synthetic frames;
only the file write and the store call touch the outside world.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import cv2
import numpy as np

from benchhud.instruments.telemetry import TelemetrySnapshot
from benchhud.jobs.store import JobNote, JobStore
from benchhud.overlay.composite import colorize
from benchhud.thermal.analyze import hottest


@dataclass(frozen=True)
class CaptureContext:
    """Whatever the bench currently has — any part may be missing."""

    scope: np.ndarray | None = None  # BGR frame
    temps: np.ndarray | None = None  # per-pixel °C
    snapshot: TelemetrySnapshot | None = None


def summarize(context: CaptureContext) -> str:
    """One-line summary of the readings, e.g. ``12.05 V · 0.42 A · 5.1 W · max 84.2°C``."""
    parts: list[str] = []
    snapshot = context.snapshot
    if snapshot is not None and snapshot.ok:
        if snapshot.voltage is not None and snapshot.current is not None:
            power = snapshot.voltage * snapshot.current
            parts += [
                f"{snapshot.voltage:.2f} V",
                f"{snapshot.current:.2f} A",
                f"{power:.1f} W",
            ]
        if snapshot.dmm is not None:
            dmm = snapshot.dmm
            shown = f"{dmm.value:g}" if dmm.value is not None else (dmm.status or "—")
            parts.append(f"DMM[{dmm.mode}] {shown}")
    if context.temps is not None:
        parts.append(f"max {hottest(context.temps).value:.1f}°C")
    return " · ".join(parts) if parts else "capture"


def compose_image(
    context: CaptureContext,
    tmin: float = 20.0,
    tmax: float = 100.0,
    colormap: int = cv2.COLORMAP_INFERNO,
) -> np.ndarray | None:
    """Scope and thermal side by side (whichever exist), matched in height."""
    tiles: list[np.ndarray] = []
    if context.scope is not None:
        tiles.append(context.scope)
    if context.temps is not None:
        tiles.append(colorize(context.temps, tmin, tmax, colormap))
    if not tiles:
        return None

    height = max(tile.shape[0] for tile in tiles)
    scaled = [
        cv2.resize(tile, (max(1, round(tile.shape[1] * height / tile.shape[0])), height))
        for tile in tiles
    ]
    return np.hstack(scaled)


def _default_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")


def capture_to_job(
    store: JobStore,
    job_id: int,
    context: CaptureContext,
    *,
    stamp: Callable[[], str] | None = None,
) -> JobNote:
    """Write the capture image (if any) and append a summary note to the job."""
    image = compose_image(context)
    attachment: str | None = None
    if image is not None:
        directory = store.attachments_dir(job_id)
        if directory is not None:  # in-memory stores have nowhere to write
            path = directory / f"{(stamp or _default_stamp)()}.png"
            cv2.imwrite(str(path), image)
            attachment = str(path)
    return store.add_note(job_id, summarize(context), attachment=attachment)
