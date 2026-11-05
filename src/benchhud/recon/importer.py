"""Make a bench-recon engagement the active one on the bench.

Unlike a ticket (which becomes a local job), an engagement leaves nothing
behind on the bench but its ref — benchhud stores only the handle and reads the
ref-safe projection live. So "Import from engagement…" is just: resolve the
chosen ref against the source, and if it resolves, it becomes the active
engagement the recon panels work against.
"""

from __future__ import annotations

from benchhud.recon.source import Engagement, ReconSource


def resolve_engagement(source: ReconSource, ref: str) -> Engagement | None:
    """The ref-safe projection for ``ref``, or ``None`` if the source hasn't got it.

    The "Import from engagement…" action calls this to confirm a chosen ref
    resolves before benchhud makes it the active engagement.
    """
    return source.get_engagement(ref)
