"""The durable-asset seam: Null / InMemory / Http sources and window wiring.

The HTTP layer is driven through an injected fake opener, so nothing here
touches a socket — mirroring the inventory and recon source tests.
"""

from __future__ import annotations

import json
import urllib.error

from benchhud.assets import Asset, HttpAssetSource, InMemoryAssetSource, NullAssetSource
from benchhud.assets.http_source import _asset_from_dict
from benchhud.config import Config
from benchhud.shell.main_window import MainWindow

_JLINK = Asset(
    id=11,
    name="J-Link #1",
    asset_type="debugger",
    asset_type_label="JTAG/SWD debugger",
    manufacturer="SEGGER",
    serial="000621234",
    status="assigned",
    assigned_ref="R26-0004",
    url="http://parts/assets/11",
)
_HACKRF = Asset(id=12, name="HackRF One", asset_type="sdr", status="available")


class _FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _opener(routes: dict[str, str], *, record: list | None = None):
    def _open(request, _timeout):
        if record is not None:
            record.append(request)
        for suffix, body in routes.items():
            if request.full_url.endswith(suffix):
                return _FakeResponse(body)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    return _open


def _raising_opener(exc: Exception):
    def _open(_request, _timeout):
        raise exc

    return _open


# --- parsing -------------------------------------------------------------


def test_asset_from_dict_maps_known_keys_and_ignores_extras() -> None:
    asset = _asset_from_dict(
        {"id": 11, "name": "J-Link #1", "asset_type": "debugger", "notes": "dropped"}
    )
    assert asset.id == 11
    assert asset.asset_type == "debugger"
    assert not hasattr(asset, "notes")  # a key benchhud doesn't model is dropped


def test_asset_from_dict_defaults_absent_optionals() -> None:
    asset = _asset_from_dict({"id": 12, "name": "HackRF One"})
    assert asset.serial == ""
    assert asset.assigned_ref == ""
    assert asset.url is None


# --- NullAssetSource -----------------------------------------------------


def test_null_source_is_empty() -> None:
    source = NullAssetSource()
    assert source.name == "none"
    assert source.list_assets() == []
    assert source.list_assets(assigned_ref="R26-0004") == []
    assert source.get_asset(11) is None


# --- InMemoryAssetSource -------------------------------------------------


def test_in_memory_filters_by_assigned_ref() -> None:
    source = InMemoryAssetSource(assets=[_JLINK, _HACKRF])
    assert [a.id for a in source.list_assets(assigned_ref="R26-0004")] == [11]
    assert source.list_assets(assigned_ref="R26-9999") == []


def test_in_memory_filters_by_asset_type_and_gets_by_id() -> None:
    source = InMemoryAssetSource(assets=[_JLINK, _HACKRF])
    assert [a.id for a in source.list_assets(asset_type="sdr")] == [12]
    assert source.get_asset(11) == _JLINK
    assert source.get_asset(99) is None


# --- HttpAssetSource -----------------------------------------------------


def test_list_assets_parses_and_sends_the_assigned_ref_filter() -> None:
    record: list = []
    source = HttpAssetSource(
        "http://parts:8000",
        "tok",
        opener=_opener(
            {"/api/assets?assigned_ref=R26-0004": json.dumps([_asdict(_JLINK)])}, record=record
        ),
    )
    assets = source.list_assets(assigned_ref="R26-0004")
    assert [a.id for a in assets] == [11]
    assert record[0].get_header("Authorization") == "Bearer tok"
    assert "assigned_ref=R26-0004" in record[0].full_url


def test_list_assets_empty_when_unreachable() -> None:
    source = HttpAssetSource(
        "http://parts:8000", "tok", opener=_raising_opener(urllib.error.URLError("down"))
    )
    assert source.list_assets() == []


def test_get_asset_parses_one_and_404_is_none() -> None:
    source = HttpAssetSource(
        "http://parts:8000",
        "tok",
        opener=_opener({"/api/assets/11": json.dumps(_asdict(_JLINK))}),
    )
    assert source.get_asset(11) is not None
    assert source.get_asset(404) is None


# --- MainWindow wiring (shares the bench-parts / inventory connection) ----


def test_window_uses_null_asset_source_without_inventory_config(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window._asset_source, NullAssetSource)


def test_window_builds_http_asset_source_from_inventory_config(qtbot) -> None:
    cfg = Config()
    cfg.inventory.base_url = "http://parts:8000"
    cfg.inventory.token = "tok"
    window = MainWindow(cfg)
    qtbot.addWidget(window)
    assert isinstance(window._asset_source, HttpAssetSource)


def test_reconfigure_asset_source_picks_up_config_change(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window._asset_source, NullAssetSource)
    window._config.inventory.base_url = "http://parts:8000"
    window.reconfigure_asset_source()
    assert isinstance(window._asset_source, HttpAssetSource)


def _asdict(asset: Asset) -> dict:
    from dataclasses import asdict

    return asdict(asset)
