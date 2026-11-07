"""Track E — live cross-repo acceptance for the recon data path.

benchhud's recon work (Tracks C/D) was built and unit-tested against fakes. This
is the on-cluster proof: run against the *live* bench-recon and bench-parts
services and show, as a pass/fail report, that benchhud reads the engagement
path and that **no sensitive field can reach a stream surface**. See issue #323.

The harness makes four kinds of check, strongest guarantee first:

* **On-air enforcement** (``on_air_enforcement``) — the one that matters most and
  needs no network. It drives the real :func:`benchhud.output.onair.compose_on_air`
  with layers tagged per the recon panels' policy and proves a *sensitive* layer's
  bytes never reach the frame handed to the sink, while the local preview keeps
  them. This is the whole stream-safety guarantee, exercised end to end.
* **Panel policy** (``panel_policy``) — the recon panels still carry the intended
  sensitivity tags (engagement/capture sensitive; interfaces/pinout stream-safe),
  so the enforcement above is applied to the right surfaces. Also network-free.
* **Projection boundary** (``check_projection``) — the live API returns only
  ref-safe fields. Any key outside the whitelist is a leak: this is where "the
  client, scope, authorization, targets and findings stay in bench-recon" is
  proven against the real service, not assumed.
* **Preflight** (``preflight``) — both services are live (``/healthz``) and
  enforce auth (``/api`` is 401 without a token, 200 with). Context for the rest.

benchhud is **read-only** on both backends — assign/return and engagement
authoring belong to the shop tablet and bench-recon's own UI — so this runs
against an *existing* engagement ref and never writes to a system of record.

The network is reached through an injectable :class:`Probe` (the same pattern the
HTTP sources use for their opener), so the assertion logic is covered by a
network-free unit test and the live run is operator-invoked.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from benchhud.assets.http_source import HttpAssetSource
from benchhud.output.onair import Layer, compose_on_air, compose_preview, excluded
from benchhud.recon.http_source import HttpReconSource

# -- ref-safe whitelists ----------------------------------------------------
#
# The exact fields each projection is allowed to carry. These mirror
# bench-recon's ``project_engagement`` / ``project_artifact`` and bench-parts'
# asset projection. Any key the live service returns that is *not* here is, by
# definition, a field that was not meant to cross into a streamable surface.

ENGAGEMENT_FIELDS = frozenset({"ref", "title", "target_class", "status"})
ARTIFACT_FIELDS = frozenset({"name", "kind", "size", "status"})
ASSET_FIELDS = frozenset(
    {
        "id",
        "name",
        "asset_type",
        "asset_type_label",
        "manufacturer",
        "model",
        "serial",
        "location",
        "status",
        "assigned_ref",
        "url",
    }
)


# -- result model -----------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One acceptance check and its outcome.

    ``skipped`` marks a check that could not run (a service not configured or not
    reachable) rather than one that failed — it does not count against the
    report's verdict, but it is printed so the gap is visible.
    """

    name: str
    ok: bool
    detail: str
    skipped: bool = False


@dataclass
class Report:
    """A run's checks plus its overall verdict."""

    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    def extend(self, checks: Sequence[Check]) -> None:
        self.checks.extend(checks)

    @property
    def ran(self) -> list[Check]:
        return [c for c in self.checks if not c.skipped]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.ran if not c.ok]

    @property
    def ok(self) -> bool:
        """True when every check that actually ran passed."""
        return all(c.ok for c in self.ran)


# -- raw HTTP probe ---------------------------------------------------------

#: Opens a prepared request and returns a response context manager — the same
#: injection point the HTTP sources use, so tests drive the harness without a
#: socket.
Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


@dataclass
class Probe:
    """A raw GET that reports the HTTP status.

    The HTTP sources deliberately swallow status (a failure degrades to an empty
    list so the HUD never crashes). The preflight and the ref-safety boundary
    need the opposite — they must see the 401, the 404, the unreachable — so this
    exposes ``(status, data)`` with ``status is None`` meaning "did not connect".
    """

    opener: Opener = _default_opener
    timeout: float = 5.0

    def get(self, url: str, token: str | None = None) -> tuple[int | None, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener(request, self.timeout) as response:
                status = getattr(response, "status", None) or 200
                try:
                    return status, json.loads(response.read())
                except ValueError:
                    return status, None
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, OSError):
            return None, None


# -- preflight --------------------------------------------------------------

_API_PROBE_PATH = {
    "bench-recon": "/api/engagements",
    "bench-parts": "/api/assets",
}


def preflight(base_url: str, token: str, service: str, probe: Probe) -> list[Check]:
    """Liveness + auth-enforcement for one service."""
    root = base_url.rstrip("/")
    api = root + _API_PROBE_PATH[service]

    live_status, _ = probe.get(root + "/healthz")
    noauth_status, _ = probe.get(api)
    auth_status, _ = probe.get(api, token=token)

    return [
        Check(
            f"{service}: service live",
            live_status == 200,
            f"GET /healthz -> {live_status if live_status is not None else 'unreachable'}",
        ),
        Check(
            f"{service}: auth enforced",
            noauth_status == 401,
            f"GET {_API_PROBE_PATH[service]} without a token -> "
            f"{noauth_status if noauth_status is not None else 'unreachable'} (want 401)",
        ),
        Check(
            f"{service}: authed read",
            auth_status == 200,
            f"GET {_API_PROBE_PATH[service]} with the token -> "
            f"{auth_status if auth_status is not None else 'unreachable'} (want 200)",
        ),
    ]


# -- projection boundary ----------------------------------------------------


def check_projection(items: Any, whitelist: frozenset[str], label: str) -> Check:
    """Assert every object in a live payload carries only ref-safe fields.

    The strongest ref-safety statement there is: not "we did not find anything
    that looks sensitive" but "the service returned *nothing outside the fields
    we allow*". An extra key is named so the failure is actionable.
    """
    if not isinstance(items, list):
        return Check(label, False, f"expected a JSON array, got {type(items).__name__}")
    leaks: dict[str, list[str]] = {}
    for obj in items:
        if not isinstance(obj, dict):
            return Check(label, False, f"payload holds a non-object element: {obj!r}")
        extra = sorted(set(obj) - whitelist)
        if extra:
            ident = str(obj.get("ref") or obj.get("name") or obj.get("id") or "?")
            leaks[ident] = extra
    if leaks:
        return Check(label, False, f"fields outside the ref-safe whitelist: {leaks}")
    return Check(
        label,
        True,
        f"{len(items)} object(s); every field within {sorted(whitelist)}",
    )


# -- on-air enforcement -----------------------------------------------------


def _stamp_layer(name: str, sensitive: bool, value: int, row: int) -> Layer:
    """A layer that stamps a unique byte at a unique row when it draws.

    The stamp is a tracer: it appears in the composited frame iff the layer's
    ``draw`` actually ran. ``compose_on_air`` must never run a sensitive layer's
    draw, so a sensitive stamp must be absent from the on-air frame.
    """

    def draw(frame: np.ndarray) -> np.ndarray:
        frame[row, 0] = value
        return frame

    return Layer(name=name, draw=draw, sensitive=sensitive)


def on_air_enforcement(
    engagement_ref: str = "", artifact_count: int = 0, asset_count: int = 0
) -> list[Check]:
    """Prove ``compose_on_air`` drops sensitive layers' bytes from the sink frame.

    Builds the layer set the recon panels would offer the compositor — tagged per
    their real policy — and inspects the two frames the choke point produces: the
    ``on_air`` frame handed to the sink and the local ``preview``. A sensitive
    layer's tracer must be absent from ``on_air`` and present on ``preview``; a
    stream-safe layer's tracer must reach ``on_air``.

    The counts only label the checks with the live engagement they were run
    against; the proof itself is deterministic and needs no network, so this
    check runs even when a service is down.
    """
    # (name, sensitive, tracer value, row) — mirrors the recon panels' policy.
    specs = [
        (f"engagement[{engagement_ref or '—'}]", True, 11, 0),
        (f"captures[{artifact_count}]", True, 22, 1),
        (f"interfaces[{asset_count}]", False, 33, 2),
        ("pinout", False, 44, 3),
    ]
    layers = [_stamp_layer(name, sens, val, row) for name, sens, val, row in specs]

    base = np.zeros((len(specs), 4, 3), dtype=np.uint8)
    on_air = compose_on_air(base, layers)
    preview = compose_preview(base.copy(), layers)

    checks: list[Check] = []
    for name, sensitive, value, _row in specs:
        in_on_air = bool((on_air == value).any())
        in_preview = bool((preview == value).any())
        if sensitive:
            checks.append(
                Check(
                    f"on-air drops sensitive {name}",
                    (not in_on_air) and in_preview,
                    f"tracer {value}: reached sink={in_on_air} (want False), "
                    f"on preview={in_preview} (want True)",
                )
            )
        else:
            checks.append(
                Check(
                    f"on-air keeps stream-safe {name}",
                    in_on_air,
                    f"tracer {value}: reached sink={in_on_air} (want True)",
                )
            )

    want = tuple(name for name, sens, _v, _r in specs if sens)
    got = excluded(layers)
    checks.append(
        Check(
            "excluded() lists exactly the sensitive layers",
            got == want,
            f"excluded()={got} want={want}",
        )
    )
    return checks


# -- panel policy -----------------------------------------------------------


def panel_policy() -> list[Check]:
    """Assert the recon panels still carry their intended sensitivity tags."""
    from benchhud.shell.panels.capture import CapturePanel
    from benchhud.shell.panels.engagement import EngagementPanel
    from benchhud.shell.panels.interfaces import InterfacePanel
    from benchhud.shell.panels.pinout import PinoutPanel

    policy = [
        (EngagementPanel, True),
        (CapturePanel, True),
        (InterfacePanel, False),
        (PinoutPanel, False),
    ]
    checks: list[Check] = []
    for cls, want in policy:
        got = bool(getattr(cls, "sensitive", False))
        checks.append(
            Check(
                f"{cls.__name__}.sensitive is {want}",
                got == want,
                f"{cls.__name__}.sensitive = {got} (want {want})",
            )
        )
    return checks


# -- orchestration ----------------------------------------------------------


def run(
    recon_base: str,
    recon_token: str,
    parts_base: str,
    parts_token: str,
    ref: str | None = None,
    *,
    probe: Probe | None = None,
) -> Report:
    """Run the full acceptance and return a :class:`Report`.

    Network-free checks (panel policy, on-air enforcement) always run, so the
    harness is useful even with a service down. Live checks (preflight,
    projection boundary) run per configured/reachable service and skip cleanly
    otherwise.
    """
    probe = probe or Probe()
    report = Report()

    # Local, always-on proofs: the stream-safety guarantee and the policy it
    # is applied to. These stand even if the cluster is unreachable.
    report.extend(panel_policy())

    # Preflight each configured service.
    recon_up = parts_up = False
    if recon_base:
        pf = preflight(recon_base, recon_token, "bench-recon", probe)
        report.extend(pf)
        recon_up = all(c.ok for c in pf)
    else:
        report.add(Check("bench-recon: configured", False, "no [recon] base_url set", skipped=True))
    if parts_base:
        pf = preflight(parts_base, parts_token, "bench-parts", probe)
        report.extend(pf)
        parts_up = all(c.ok for c in pf)
    else:
        report.add(
            Check("bench-parts: configured", False, "no [inventory] base_url set", skipped=True)
        )

    # Live read-path + projection boundary (needs bench-recon up).
    engagement_ref = ref or ""
    artifact_count = asset_count = 0
    if recon_up:
        recon = HttpReconSource(recon_base, recon_token, opener=probe.opener, timeout=probe.timeout)
        if not engagement_ref:
            listed = recon.list_engagements()
            engagement_ref = listed[0].ref if listed else ""
        if engagement_ref:
            root = recon_base.rstrip("/")
            _, eng_json = probe.get(f"{root}/api/engagements/{engagement_ref}", recon_token)
            report.add(
                check_projection(
                    [eng_json] if isinstance(eng_json, dict) else eng_json,
                    ENGAGEMENT_FIELDS,
                    f"engagement {engagement_ref} is ref-safe",
                )
            )
            _, arts_json = probe.get(
                f"{root}/api/engagements/{engagement_ref}/artifacts", recon_token
            )
            report.add(
                check_projection(arts_json, ARTIFACT_FIELDS, f"artifacts {engagement_ref} ref-safe")
            )
            artifact_count = len(recon.artifacts(engagement_ref))

            if parts_up:
                assets = HttpAssetSource(
                    parts_base, parts_token, opener=probe.opener, timeout=probe.timeout
                ).list_assets(assigned_ref=engagement_ref)
                asset_count = len(assets)
                _, assets_json = probe.get(
                    f"{parts_base.rstrip('/')}/api/assets?assigned_ref={engagement_ref}",
                    parts_token,
                )
                report.add(
                    check_projection(
                        assets_json, ASSET_FIELDS, f"assets for {engagement_ref} ref-safe"
                    )
                )
        else:
            report.add(
                Check(
                    "engagement available to read",
                    False,
                    "bench-recon returned no engagements; seed one or pass --ref",
                    skipped=True,
                )
            )

    # On-air enforcement — always; labelled with the live engagement if we got one.
    report.extend(on_air_enforcement(engagement_ref, artifact_count, asset_count))
    return report
