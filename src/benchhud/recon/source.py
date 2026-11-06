"""Pluggable recon-engagement sources.

benchhud is the bench-side interface to ``bench-recon`` — the engagement/target
system of record for hardware/automotive/IoT pentesting — not the record itself.
The sensitive detail (client, scope, authorization text, findings, credentials)
lives in bench-recon and never leaves it; benchhud stores only a **ref** and
reads a **ref-safe projection** over the API.

Everything goes through :class:`ReconSource`, mirroring
:class:`~benchhud.tickets.source.TicketSource`, so bench-recon is just the first
implementation and other backends can follow. With no source configured
(:class:`NullReconSource`) benchhud runs standalone — there is simply no recon
panel data.

**Stream-safety note:** benchhud composites panels to a virtual camera. The
projection is deliberately ref-safe — :class:`Engagement` carries only a handle,
an operator-set safe title, a target *category* and a status, never an identity.
Even so, the recon panels tag their surfaces sensitive by default
(:mod:`benchhud.output.onair`), so a field never reaches the stream unless
someone has decided it is safe to show. :class:`Artifact` is metadata only —
names, sizes and status, never the dumped contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Engagement:
    """A ref-safe projection of a bench-recon engagement.

    ``ref`` is the stable external handle benchhud works against (e.g.
    ``R26-0004``). ``title`` is an operator-set, explicitly non-sensitive label;
    ``target_class`` is a category (``automotive-ecu``, ``iot-camera``), not an
    identity; ``status`` is the engagement's lifecycle state. All non-ref fields
    are optional so benchhud still parses a projection that omits one.
    """

    ref: str
    title: str = ""
    target_class: str = ""
    status: str = ""


@dataclass(frozen=True)
class Artifact:
    """Metadata for a capture/firmware-dump artifact — never its contents.

    ``name`` is the file/label, ``kind`` its type (``firmware-dump``,
    ``capture``), ``size`` the byte count, ``status`` the progress
    (``complete``, ``in-progress``). All but ``name`` are optional.
    """

    name: str
    kind: str = ""
    size: int = 0
    status: str = ""


@runtime_checkable
class ReconSource(Protocol):
    """Something benchhud can pull a ref-safe engagement projection from."""

    @property
    def name(self) -> str:
        """Human label for the backend (shown in the UI)."""
        ...

    def list_engagements(self) -> list[Engagement]: ...

    def get_engagement(self, ref: str) -> Engagement | None: ...

    def artifacts(self, ref: str) -> list[Artifact]: ...


class NullReconSource:
    """Default: no recon backend configured — benchhud runs standalone."""

    name = "none"

    def list_engagements(self) -> list[Engagement]:
        return []

    def get_engagement(self, ref: str) -> Engagement | None:
        return None

    def artifacts(self, ref: str) -> list[Artifact]:
        return []


@dataclass
class InMemoryReconSource:
    """A source backed by lists — for tests, demos, and offline fallback."""

    engagements: list[Engagement] = field(default_factory=list)
    artifacts_by_ref: dict[str, list[Artifact]] = field(default_factory=dict)
    name: str = "in-memory"

    def list_engagements(self) -> list[Engagement]:
        return list(self.engagements)

    def get_engagement(self, ref: str) -> Engagement | None:
        return next((e for e in self.engagements if e.ref == ref), None)

    def artifacts(self, ref: str) -> list[Artifact]:
        return list(self.artifacts_by_ref.get(ref, []))
