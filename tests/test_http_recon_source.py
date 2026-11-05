"""Tests for the bench-recon-backed HTTP recon source (hardware-free, no network).

The HTTP layer is driven through an injected fake opener, so nothing here
touches a socket; parsing and graceful-degradation are exercised directly —
mirroring the intake ticket source's tests.
"""

from __future__ import annotations

import json
import urllib.error

from benchhud.config import Config
from benchhud.recon import HttpReconSource, NullReconSource
from benchhud.recon.http_source import _artifact_from_dict, _engagement_from_dict
from benchhud.shell.main_window import MainWindow

_ENGAGEMENTS = [
    {
        "ref": "R26-0004",
        "title": "Smart lock teardown",
        "target_class": "iot-lock",
        "status": "active",
    },
    {"ref": "R26-0005"},  # projection that omits the optional fields
]
_ARTIFACTS = [
    {"name": "flash.bin", "kind": "firmware-dump", "size": 8388608, "status": "complete"},
    {"name": "can-trace.log", "kind": "capture", "size": 1024},
]


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
    """A fake opener that serves canned JSON by URL suffix, else 404s."""

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


def test_engagement_from_dict_maps_known_keys_and_ignores_extras() -> None:
    engagement = _engagement_from_dict({**_ENGAGEMENTS[0], "client": "LEAK"})
    assert engagement.ref == "R26-0004"
    assert engagement.target_class == "iot-lock"
    assert not hasattr(engagement, "client")  # an unknown key never lands on the dataclass


def test_engagement_from_dict_defaults_absent_optionals() -> None:
    engagement = _engagement_from_dict(_ENGAGEMENTS[1])
    assert engagement.ref == "R26-0005"
    assert engagement.title == ""
    assert engagement.target_class == ""
    assert engagement.status == ""


def test_artifact_from_dict_defaults_absent_optionals() -> None:
    artifact = _artifact_from_dict(_ARTIFACTS[1])
    assert artifact.name == "can-trace.log"
    assert artifact.status == ""
    assert artifact.size == 1024


# --- list_engagements ----------------------------------------------------


def test_list_engagements_parses_the_projection() -> None:
    source = HttpReconSource(
        "http://recon:8000",
        "tok",
        opener=_opener({"/api/engagements": json.dumps(_ENGAGEMENTS)}),
    )
    engagements = source.list_engagements()
    assert [e.ref for e in engagements] == ["R26-0004", "R26-0005"]
    assert engagements[0].title == "Smart lock teardown"


def test_list_engagements_sends_bearer_token() -> None:
    record: list = []
    source = HttpReconSource(
        "http://recon:8000",
        "s3cret",
        opener=_opener({"/api/engagements": json.dumps([])}, record=record),
    )
    source.list_engagements()
    assert record[0].get_header("Authorization") == "Bearer s3cret"


def test_list_engagements_empty_when_unreachable() -> None:
    source = HttpReconSource(
        "http://recon:8000", "tok", opener=_raising_opener(urllib.error.URLError("down"))
    )
    assert source.list_engagements() == []


# --- get_engagement ------------------------------------------------------


def test_get_engagement_parses_one() -> None:
    source = HttpReconSource(
        "http://recon:8000",
        "tok",
        opener=_opener({"/api/engagements/R26-0004": json.dumps(_ENGAGEMENTS[0])}),
    )
    engagement = source.get_engagement("R26-0004")
    assert engagement is not None
    assert engagement.status == "active"


def test_get_engagement_404_is_none() -> None:
    source = HttpReconSource("http://recon:8000", "tok", opener=_opener({}))
    assert source.get_engagement("missing") is None


# --- artifacts -----------------------------------------------------------


def test_artifacts_parses_the_metadata_list() -> None:
    source = HttpReconSource(
        "http://recon:8000",
        "tok",
        opener=_opener({"/api/engagements/R26-0004/artifacts": json.dumps(_ARTIFACTS)}),
    )
    artifacts = source.artifacts("R26-0004")
    assert [a.name for a in artifacts] == ["flash.bin", "can-trace.log"]
    assert artifacts[0].kind == "firmware-dump"


def test_artifacts_empty_when_unreachable() -> None:
    source = HttpReconSource("http://recon:8000", "tok", opener=_raising_opener(OSError("boom")))
    assert source.artifacts("R26-0004") == []


def test_trailing_slash_in_base_url_is_handled() -> None:
    record: list = []
    source = HttpReconSource(
        "http://recon:8000/",
        "tok",
        opener=_opener({"/api/engagements": json.dumps([])}, record=record),
    )
    source.list_engagements()
    assert record[0].full_url == "http://recon:8000/api/engagements"


# --- config + MainWindow wiring ------------------------------------------


def test_recon_config_round_trips() -> None:
    cfg = Config()
    cfg.recon.base_url = "http://recon:8000"
    cfg.recon.token = "abc"
    restored = Config.from_dict(cfg.to_dict())
    assert restored.recon.base_url == "http://recon:8000"
    assert restored.recon.token == "abc"


def test_window_uses_null_source_without_recon_config(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window._recon_source, NullReconSource)


def test_window_builds_http_source_when_configured(qtbot) -> None:
    cfg = Config()
    cfg.recon.base_url = "http://recon:8000"
    cfg.recon.token = "tok"
    window = MainWindow(cfg)
    qtbot.addWidget(window)
    assert isinstance(window._recon_source, HttpReconSource)


def test_reconfigure_recon_source_picks_up_config_change(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window._recon_source, NullReconSource)
    window._config.recon.base_url = "http://recon:8000"
    window.reconfigure_recon_source()
    assert isinstance(window._recon_source, HttpReconSource)
