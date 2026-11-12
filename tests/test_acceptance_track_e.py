"""Tests for the Track E acceptance harness (hardware-free, no network).

The harness talks to the cluster only through an injected :class:`Probe` opener,
so everything here runs against a fake cluster: the on-air enforcement proof, the
panel-sensitivity policy, the ref-safety boundary, and the full ``run`` both clean
and with a deliberately leaky projection. See issue #323.
"""

from __future__ import annotations

import json
import urllib.error

from benchhud.acceptance import (
    ARTIFACT_FIELDS,
    ENGAGEMENT_FIELDS,
    Probe,
    check_projection,
    on_air_enforcement,
    panel_policy,
    preflight,
    run,
)

RECON = "http://recon.test"
PARTS = "http://parts.test"

_ENGAGEMENTS = [
    {
        "ref": "R26-0004",
        "title": "Smart lock teardown",
        "target_class": "iot-lock",
        "status": "active",
    },
]
_ARTIFACTS = [
    {"name": "flash.bin", "kind": "firmware-dump", "size": 8388608, "status": "stored"},
]
_ASSETS = [
    {
        "id": 7,
        "name": "JTAG probe",
        "asset_type": "debugger",
        "serial": "J-1",
        "status": "assigned",
        "assigned_ref": "R26-0004",
    },
]


class _Resp:
    def __init__(self, body: str, status: int = 200) -> None:
        self._body = body.encode()
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _opener(
    *,
    engagements=None,
    artifacts=None,
    assets=None,
    recon_down: bool = False,
    parts_down: bool = False,
):
    engagements = _ENGAGEMENTS if engagements is None else engagements
    artifacts = _ARTIFACTS if artifacts is None else artifacts
    assets = _ASSETS if assets is None else assets

    def _open(request, _timeout):
        url = request.full_url
        if (recon_down and url.startswith(RECON)) or (parts_down and url.startswith(PARTS)):
            raise urllib.error.URLError("unreachable")
        if url.endswith("/healthz"):
            return _Resp("ok")
        if "/api/" in url and not request.has_header("Authorization"):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        if url.rstrip("/").endswith("/api/engagements"):
            return _Resp(json.dumps(engagements))
        if "/api/engagements/" in url and url.endswith("/artifacts"):
            return _Resp(json.dumps(artifacts))
        if "/api/engagements/" in url:
            return _Resp(json.dumps(engagements[0]))
        if "/api/assets" in url:
            return _Resp(json.dumps(assets))
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    return _open


def _probe(**kw) -> Probe:
    return Probe(opener=_opener(**kw))


# --- on-air enforcement (the core guarantee) -------------------------------


def test_on_air_enforcement_drops_sensitive_keeps_safe():
    checks = on_air_enforcement("R26-0004", artifact_count=1, asset_count=1)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    # sensitive surfaces are named as dropped; stream-safe ones as kept
    dropped = [c.name for c in checks if c.name.startswith("on-air drops sensitive")]
    kept = [c.name for c in checks if c.name.startswith("on-air keeps stream-safe")]
    assert any("engagement" in n for n in dropped)
    assert any("captures" in n for n in dropped)
    assert any("interfaces" in n for n in kept)
    assert any("pinout" in n for n in kept)


# --- panel policy ----------------------------------------------------------


def test_panel_policy_matches_intended_tags():
    checks = panel_policy()
    assert all(c.ok for c in checks), [c.detail for c in checks if not c.ok]
    names = {c.name for c in checks}
    assert "EngagementPanel.sensitive is True" in names
    assert "InterfacePanel.sensitive is False" in names


# --- projection boundary ---------------------------------------------------


def test_check_projection_passes_on_clean_payload():
    assert check_projection(_ENGAGEMENTS, ENGAGEMENT_FIELDS, "clean").ok
    assert check_projection(_ARTIFACTS, ARTIFACT_FIELDS, "clean").ok


def test_check_projection_flags_a_leaked_field():
    leaky = [{**_ENGAGEMENTS[0], "client_ref": "ACME Corp", "scope": "full"}]
    result = check_projection(leaky, ENGAGEMENT_FIELDS, "leaky")
    assert not result.ok
    assert "client_ref" in result.detail
    assert "scope" in result.detail


def test_check_projection_rejects_non_array():
    assert not check_projection({"ref": "x"}, ENGAGEMENT_FIELDS, "shape").ok


# --- preflight -------------------------------------------------------------


def test_preflight_all_green():
    checks = preflight(RECON, "tok", "bench-recon", _probe())
    assert all(c.ok for c in checks), [c.detail for c in checks if not c.ok]


def test_preflight_reports_a_down_service():
    checks = preflight(RECON, "tok", "bench-recon", _probe(recon_down=True))
    assert not any(c.ok for c in checks)
    assert "unreachable" in checks[0].detail


# --- full run --------------------------------------------------------------


def test_run_clean_cluster_passes_everything():
    report = run(RECON, "rtok", PARTS, "ptok", probe=_probe())
    assert report.ok, report.failures
    labels = " ".join(c.name for c in report.checks)
    assert "ref-safe" in labels  # the live projection boundary ran
    assert "on-air drops sensitive" in labels


def test_run_detects_a_projection_leak():
    leaky = [{**_ENGAGEMENTS[0], "authorization": "signed SOW text"}]
    report = run(RECON, "rtok", PARTS, "ptok", probe=_probe(engagements=leaky))
    assert not report.ok
    assert any("authorization" in c.detail for c in report.failures)


def test_run_with_recon_down_still_proves_on_air_and_policy():
    report = run(RECON, "rtok", PARTS, "ptok", probe=_probe(recon_down=True))
    assert not report.ok  # recon preflight failed
    # but the enforcement proof and policy still ran and passed
    onair = [c for c in report.checks if c.name.startswith("on-air")]
    policy = [c for c in report.checks if ".sensitive is" in c.name]
    assert onair and all(c.ok for c in onair)
    assert policy and all(c.ok for c in policy)


def test_run_unconfigured_services_skip_cleanly():
    report = run("", "", "", "", probe=_probe())
    # no live checks ran, but local proofs did and pass → verdict holds on what ran
    assert report.ok
    assert any(c.skipped for c in report.checks)
    assert all(c.ok for c in report.checks if not c.skipped)
