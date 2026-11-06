"""On-air sensitivity model — what may be composited into the live stream.

benchhud began with one exclusion rule: the ticket ``customer`` field never
reaches a stream surface. It held because the composite frame simply never had
customer text drawn into it, and the browser sources whitelist their fields.
That is too coarse for the recon work, which needs the same guarantee for a
wider, tagged set — target identity, client name, credentials, findings, raw
serial-console text — while the operator still sees all of it locally.

This module generalises the single rule into a **tagged** one. A :class:`Layer`
is a drawable surface offered to the compositor; its ``sensitive`` tag is the
whole answer to the review question "could this reach the virtual camera?".
:func:`compose_preview` draws every layer — the full picture on the operator's
screen — and :func:`compose_on_air` draws only the non-sensitive ones, so a
sensitive layer's pixels never reach the output sink.

The split lives at the one choke point every composite flows through
(:meth:`benchhud.shell.main_window.MainWindow._show_composite`). Sensitivity
defaults **safe** for the bench surfaces that predate this model (scope,
thermal, the telemetry/graph overlays) and **sensitive** for recon surfaces —
see :attr:`benchhud.shell.panel.Panel.sensitive` — so a new recon panel stays
off air until it explicitly declares itself stream-safe. The default must be
safe-for-sensitive precisely because every new panel is a place to forget the
tag; the regression test in ``tests/test_onair.py`` is what keeps the forgetting
from reaching a stream.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

#: A paint step: take a BGR frame, return a BGR frame. A layer may draw in place
#: and return the same array, or return a new one — callers never assume which.
Draw = Callable[[np.ndarray], np.ndarray]


@dataclass(frozen=True)
class Layer:
    """One surface offered to the compositor, with its on-air sensitivity.

    ``draw`` paints the surface onto a BGR frame and returns it. A ``sensitive``
    layer is shown on the local preview only: :func:`compose_on_air` skips it,
    so its pixels cannot reach the output sink.
    """

    name: str
    draw: Draw
    sensitive: bool = False


def compose_preview(base: np.ndarray, layers: Sequence[Layer] = ()) -> np.ndarray:
    """Draw every layer — the full picture the operator sees locally."""
    frame = base
    for layer in layers:
        frame = layer.draw(frame)
    return frame


def compose_on_air(base: np.ndarray, layers: Sequence[Layer] = ()) -> np.ndarray:
    """Draw only the non-sensitive layers — the picture the stream is allowed.

    This is the enforcement point. Anything with ``sensitive=True`` is dropped
    here and never touches the frame handed to the output sink.
    """
    frame = base
    for layer in layers:
        if layer.sensitive:
            continue
        frame = layer.draw(frame)
    return frame


def excluded(layers: Sequence[Layer]) -> tuple[str, ...]:
    """Names of the layers :func:`compose_on_air` drops — for logs and review."""
    return tuple(layer.name for layer in layers if layer.sensitive)
