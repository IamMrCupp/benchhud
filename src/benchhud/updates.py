"""Is there a newer benchhud? (#560, the first step of #224.)

This only finds out. It never downloads or installs anything, and never runs
``sudo``: installing is apt's job on Linux, and the installers on the release
page everywhere else.

Where it looks, as decided on #224:

- **Linux with the bench apt source**: apt's candidate for ``benchhud``, read
  from ``apt-cache policy``. No network: Ubuntu's ``apt-daily`` timer keeps the
  lists fresh. It never announces a version apt can't install yet.
- **Everything else**: where ``github.com/…/releases/latest`` redirects to.
  That's a web redirect, not the REST API, so it isn't counted against the API's
  60 unauthenticated requests an hour.
"""

from __future__ import annotations

import html
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from benchhud import __version__

log = logging.getLogger(__name__)

RELEASES_URL = "https://github.com/IamMrCupp/benchhud/releases"
LATEST_URL = f"{RELEASES_URL}/latest"
#: The bench apt repo (bench-suite's ``apt repo`` workflow publishes it).
APT_HOST = "apt.yolorepair.com"
TIMEOUT_S = 10.0

_TAG = re.compile(r"/releases/tag/v?(?P<version>[0-9][^/?#]*)$")


class UpdateCheckError(Exception):
    """The check couldn't get an answer; the message says why."""


@dataclass(frozen=True)
class UpdateResult:
    """What one check found.

    ``latest`` is the newest version it could see, or None when it couldn't
    see one (``error`` says why). ``via`` is ``"apt"`` or ``"github"``.
    """

    current: str
    latest: str | None
    via: str
    error: str | None = None

    @property
    def available(self) -> bool:
        """True when ``latest`` is newer than what's running."""
        if self.latest is None:
            return False
        latest, current = version_key(self.latest), version_key(self.current)
        return latest is not None and current is not None and latest > current

    @property
    def url(self) -> str:
        """The release page for ``latest``, or the releases list without one."""
        return f"{RELEASES_URL}/tag/v{self.latest}" if self.latest else RELEASES_URL

    @property
    def how(self) -> str:
        """How to install it, in a few words."""
        if self.via == "apt":
            return "sudo apt update && sudo apt upgrade"
        if sys.platform == "darwin":
            return "brew upgrade --cask benchhud, or the .dmg from the release page"
        if sys.platform == "win32":
            return "the .msi from the release page"
        return "the .deb from the release page, or the bench apt repo (see the README)"


def upstream(version: str) -> str:
    """The upstream part of a Debian version: no epoch, revision or ``~`` suffix."""
    version = version.strip()
    version = version.split(":", 1)[1] if ":" in version else version
    return re.split(r"[-~+]", version, maxsplit=1)[0]


def version_key(version: str) -> tuple[int, ...] | None:
    """``"v0.34.3"`` → ``(0, 34, 3)``; None for anything that isn't dotted numbers."""
    text = upstream(version).removeprefix("v")
    parts = text.split(".")
    if not text or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def parse_apt_policy(text: str) -> str | None:
    """apt's candidate for benchhud, if the bench apt repo is one of its sources.

    Without that repo in the version table, a candidate is only what's already
    installed (``/var/lib/dpkg/status``), which says nothing about updates.
    """
    if APT_HOST not in text:
        return None
    match = re.search(r"^\s*Candidate:\s*(\S+)", text, re.MULTILINE)
    if match is None or match.group(1) == "(none)":
        return None
    return upstream(match.group(1))


def apt_policy() -> str | None:
    """``apt-cache policy benchhud``'s output, or None where there's no apt."""
    try:
        done = subprocess.run(
            ["apt-cache", "policy", "benchhud"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # hand the 302 back instead of following it


def _open_latest() -> str:
    """The ``Location`` that ``releases/latest`` redirects to."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(
        LATEST_URL, method="HEAD", headers={"User-Agent": f"benchhud/{__version__}"}
    )
    try:
        with opener.open(request, timeout=TIMEOUT_S) as response:
            raise UpdateCheckError(f"GitHub answered {response.status}, not a redirect")
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return exc.headers.get("Location", "")
        raise UpdateCheckError(f"GitHub answered {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateCheckError(f"couldn't reach GitHub ({reason})") from exc


def parse_latest_location(location: str) -> str:
    """The version in ``…/releases/tag/v0.34.3``."""
    match = _TAG.search(location.strip())
    if match is None:
        raise UpdateCheckError(f"GitHub's latest release isn't a version: {location or '(none)'}")
    return match.group("version")


def github_latest(open_latest: Callable[[], str] = _open_latest) -> str:
    """The newest release's version, from where ``releases/latest`` points."""
    return parse_latest_location(open_latest())


def check(
    current: str = __version__,
    *,
    platform: str | None = None,
    policy: Callable[[], str | None] = apt_policy,
    latest: Callable[[], str] = github_latest,
) -> UpdateResult:
    """Find the newest benchhud. Blocks for up to :data:`TIMEOUT_S`; never raises."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        text = policy()
        candidate = parse_apt_policy(text) if text else None
        if candidate is not None:
            return UpdateResult(current, candidate, "apt")
    try:
        return UpdateResult(current, latest(), "github")
    except UpdateCheckError as exc:
        return UpdateResult(current, None, "github", str(exc))
    except Exception as exc:  # a check must never take the window down
        log.exception("update check failed")
        return UpdateResult(current, None, "github", str(exc) or type(exc).__name__)


def update_answer_html(result: UpdateResult) -> str:
    """What Help → Check for updates says about ``result``, as rich text."""
    current = html.escape(result.current, quote=False)
    releases = f'<a href="{html.escape(result.url)}">release notes and downloads</a>'
    if result.error is not None:
        return (
            f"<p>Couldn't check for updates: {html.escape(result.error, quote=False)}.</p>"
            f'<p>This is benchhud {current}. The <a href="{RELEASES_URL}">releases page</a>'
            " has the newest one.</p>"
        )
    latest = html.escape(result.latest or "", quote=False)
    if result.available:
        how = html.escape(result.how, quote=False)
        where = " apt has it." if result.via == "apt" else ""
        return (
            f"<h3>benchhud {latest} is out</h3>"
            f"<p>This is {current}.{where} Install it with <code>{how}</code>"
            " — benchhud doesn't install anything itself.</p>"
            f"<p>{releases}</p>"
        )
    ahead = version_key(result.current) or (), version_key(result.latest or "") or ()
    if ahead[0] > ahead[1]:
        return f"<p>This is benchhud {current}, newer than the latest release ({latest}).</p>"
    newest = "the newest in the bench apt repo" if result.via == "apt" else "the newest release"
    return f"<p>benchhud {current} is {newest}.</p><p>{releases}</p>"
