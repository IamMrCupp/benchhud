"""Pluggable durable-asset sources.

bench-parts tracks two kinds of stock: *consumable* parts (counted, consumed
against a job — the :mod:`benchhud.inventory` seam) and *durable* assets (test
gear you assign to an engagement and get back: SDRs, logic analyzers, JTAG/UART
debuggers, CAN interfaces…). This seam is the bench-side read client for the
durable side, mirroring :mod:`benchhud.inventory.source`: a Protocol, a Null
default, and an in-memory fake, so the HTTP adapter is just the first backend.

It exists so the connected-interfaces monitor can ask "what gear is assigned to
this engagement, and which attached port is it?" — the cross-map in Track C's
interface panel. It is **read-only**: assign/return are writes the shop tablet
owns; benchhud only reads here.

Asset data carries no PII, same as parts. ``serial`` is the identity that
survives when VID:PID can't tell two identical probes apart — the panel binds a
detected port to an asset by it (or by a manual choice), never by VID:PID alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Asset:
    """A durable asset as bench-parts reports it.

    ``asset_type`` is the slug (``debugger``, ``sdr``…); ``asset_type_label`` is
    its human form. ``status`` is one of ``available`` / ``assigned`` /
    ``out_for_repair`` / ``retired``; ``assigned_ref`` is the engagement ref it
    is assigned to, non-empty only while ``status == "assigned"``. ``serial`` is
    the cross-map discriminator. All but ``id`` and ``name`` are optional so a
    projection that omits a field still parses.
    """

    id: int
    name: str
    asset_type: str = ""
    asset_type_label: str = ""
    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    location: str = ""
    status: str = ""
    assigned_ref: str = ""
    url: str | None = None


@runtime_checkable
class AssetSource(Protocol):
    """Something benchhud can read the durable-asset registry from."""

    @property
    def name(self) -> str:
        """Human label for the backend (shown in the UI)."""
        ...

    def list_assets(
        self, assigned_ref: str | None = None, asset_type: str | None = None
    ) -> list[Asset]: ...

    def get_asset(self, asset_id: int) -> Asset | None: ...


class NullAssetSource:
    """Default: no asset backend configured — benchhud runs standalone."""

    name = "none"

    def list_assets(
        self, assigned_ref: str | None = None, asset_type: str | None = None
    ) -> list[Asset]:
        return []

    def get_asset(self, asset_id: int) -> Asset | None:
        return None


@dataclass
class InMemoryAssetSource:
    """A source backed by a list — for tests, demos, and offline fallback."""

    assets: list[Asset] = field(default_factory=list)
    name: str = "in-memory"

    def list_assets(
        self, assigned_ref: str | None = None, asset_type: str | None = None
    ) -> list[Asset]:
        return [
            a
            for a in self.assets
            if (assigned_ref is None or a.assigned_ref == assigned_ref)
            and (asset_type is None or a.asset_type == asset_type)
        ]

    def get_asset(self, asset_id: int) -> Asset | None:
        return next((a for a in self.assets if a.id == asset_id), None)
