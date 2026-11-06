"""Durable-asset seam — benchhud's read client for bench-parts' asset registry."""

from benchhud.assets.http_source import HttpAssetSource
from benchhud.assets.source import (
    Asset,
    AssetSource,
    InMemoryAssetSource,
    NullAssetSource,
)

__all__ = [
    "Asset",
    "AssetSource",
    "HttpAssetSource",
    "InMemoryAssetSource",
    "NullAssetSource",
]
