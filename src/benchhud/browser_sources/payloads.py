"""What each browser-source page receives: plain JSON-ready dicts.

Built from the same objects the Qt surfaces draw from, so a page can't disagree
with the dock: the meter takes the dock's own formatted strings
(:class:`MeterReadout`), and the graph takes the on-stream graph's pure model
(:func:`build_graph_model`), so the per-series vertical ranges match too.

**The job card is a whitelist.** It names the fields that may go on air rather
than dropping the ones that mayn't, so a column added to :class:`Job` later
stays off stream until someone decides otherwise. ``customer`` never goes, per
the on-air rule (PII-out is the only composite exclusion); ``serial`` and
``symptoms`` stay off too, since a device serial identifies an owner and
symptoms are free text typed from a customer's description.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from benchhud.instruments.power_history import PowerSample
from benchhud.jobs.store import Job
from benchhud.overlay.graph import SERIES, build_graph_model
from benchhud.overlay.telemetry import CELL_TINT, MeterReadout
from benchhud.palette import SeriesPalette, palette, shade, tint

#: The only job fields a browser source ever sees.
JOB_FIELDS = ("id", "device", "board_id", "status", "external_ref")


def _colours(colours: SeriesPalette) -> dict[str, dict[str, str]]:
    """Line, cell fill and cell border per quantity — the dock's exact mix."""
    return {
        name: {"line": c, "fill": tint(c, CELL_TINT), "border": shade(c, 0.15)}
        for name, c in (
            ("voltage", colours.voltage),
            ("current", colours.current),
            ("power", colours.power),
            ("max_current", colours.max_current),
        )
    }


def meter_payload(readout: MeterReadout, colours: SeriesPalette | None = None) -> dict[str, Any]:
    """The meter page: the dock's formatted strings plus the quantity colours."""
    return {"readout": asdict(readout), "colours": _colours(colours or palette())}


def graph_payload(
    samples: Sequence[PowerSample],
    window_seconds: float,
    colours: SeriesPalette | None = None,
    reference: Sequence[tuple[float, float]] = (),
) -> dict[str, Any]:
    """The graph page: normalised polylines (x, y in 0..1) and the live values.

    ``reference`` is the known-good power-on curve (#263), drawn dashed.
    """
    model = build_graph_model(samples, window_seconds, reference)
    c = colours or palette()
    return {
        "window_seconds": model.window_seconds,
        "series": [
            {"label": label, "unit": unit, "colour": colour, "points": [list(p) for p in line]}
            for (label, unit), colour, line in zip(
                SERIES, (c.voltage, c.current, c.power), model.lines, strict=True
            )
        ],
        "latest": list(model.latest) if model.latest is not None else None,
        "reference": [list(p) for p in model.reference],
    }


def job_payload(job: Job | None) -> dict[str, Any]:
    """The job card: whitelisted fields only, or ``{"job": None}`` when none is selected."""
    if job is None:
        return {"job": None}
    return {"job": {name: getattr(job, name) for name in JOB_FIELDS}}
