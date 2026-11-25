"""The update check's logic (#560): where it looks and what it makes of the answer."""

from __future__ import annotations

import ssl
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from benchhud import updates
from benchhud.updates import (
    LATEST_URL,
    UpdateCheckError,
    UpdateResult,
    check,
    parse_apt_policy,
    parse_latest_location,
    update_answer_html,
    upstream,
    version_key,
)

# apt-cache policy on a machine with the bench apt repo, one release behind.
POLICY_BEHIND = """benchhud:
  Installed: 0.34.2
  Candidate: 0.34.3
  Version table:
     0.34.3 500
        500 https://apt.yolorepair.com stable/main amd64 Packages
 *** 0.34.2 500
        500 https://apt.yolorepair.com stable/main amd64 Packages
        100 /var/lib/dpkg/status
"""

# The bench on 2026-11-25: a hand-installed .deb and no bench apt source.
POLICY_NO_SOURCE = """benchhud:
  Installed: 0.34.2
  Candidate: 0.34.2
  Version table:
 *** 0.34.2 100
        100 /var/lib/dpkg/status
"""


def _no_github() -> str:
    raise AssertionError("went to GitHub")


@pytest.mark.parametrize(
    ("version", "key"),
    [
        ("0.34.3", (0, 34, 3)),
        ("v0.34.3", (0, 34, 3)),
        ("0.13.1-1~ubuntu-resolute", (0, 13, 1)),
        ("1:2.1", (2, 1)),
        ("0.34", (0, 34)),
        ("", None),
        ("latest", None),
        ("0.34.x", None),
    ],
)
def test_version_key(version: str, key: tuple[int, ...] | None) -> None:
    assert version_key(version) == key


def test_upstream_drops_epoch_and_revision() -> None:
    assert upstream("1:0.34.3-1~ubuntu-resolute") == "0.34.3"


def test_newer_is_by_number_not_by_text() -> None:
    assert UpdateResult("0.9.0", "0.10.0", "github").available
    assert not UpdateResult("0.10.0", "0.9.0", "github").available
    assert not UpdateResult("0.34.3", "0.34.3", "github").available
    assert not UpdateResult("0.34.3", None, "github", "offline").available
    assert not UpdateResult("0.34.3", "nonsense", "github").available


def test_apt_candidate_from_the_bench_repo() -> None:
    assert parse_apt_policy(POLICY_BEHIND) == "0.34.3"


def test_no_bench_apt_source_means_no_apt_answer() -> None:
    assert parse_apt_policy(POLICY_NO_SOURCE) is None
    assert parse_apt_policy("") is None
    none = POLICY_BEHIND.replace("Candidate: 0.34.3", "Candidate: (none)")
    assert parse_apt_policy(none) is None


def test_latest_location_gives_the_tag() -> None:
    url = "https://github.com/IamMrCupp/benchhud/releases/tag/v0.34.3"
    assert parse_latest_location(url) == "0.34.3"
    assert parse_latest_location("/IamMrCupp/benchhud/releases/tag/v1.0.0\n") == "1.0.0"


@pytest.mark.parametrize(
    "location", ["", "https://github.com/IamMrCupp/benchhud/releases", "https://github.com/login"]
)
def test_a_location_that_isnt_a_release_is_an_error(location: str) -> None:
    with pytest.raises(UpdateCheckError):
        parse_latest_location(location)


def test_linux_with_the_apt_repo_never_goes_online() -> None:
    result = check("0.34.2", platform="linux", policy=lambda: POLICY_BEHIND, latest=_no_github)

    assert result == UpdateResult("0.34.2", "0.34.3", "apt")
    assert result.available
    assert "apt" in result.how and "upgrade" in result.how


def test_linux_without_the_apt_repo_asks_github() -> None:
    result = check(
        "0.34.2", platform="linux", policy=lambda: POLICY_NO_SOURCE, latest=lambda: "0.34.3"
    )

    assert result == UpdateResult("0.34.2", "0.34.3", "github")


def test_linux_with_no_apt_at_all_asks_github() -> None:
    result = check("0.34.2", platform="linux", policy=lambda: None, latest=lambda: "0.34.3")

    assert result.via == "github" and result.available


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_mac_and_windows_never_run_apt(platform: str) -> None:
    def policy() -> str:
        raise AssertionError("ran apt-cache")

    result = check("0.34.2", platform=platform, policy=policy, latest=lambda: "0.34.3")

    assert result.via == "github" and result.latest == "0.34.3"


def test_a_failed_check_comes_back_as_an_error_not_an_exception() -> None:
    def offline() -> str:
        raise UpdateCheckError("couldn't reach GitHub (timed out)")

    def broken() -> str:
        raise ValueError("boom")

    assert check("0.34.2", platform="darwin", latest=offline).error == (
        "couldn't reach GitHub (timed out)"
    )
    result = check("0.34.2", platform="darwin", latest=broken)
    assert result.latest is None and result.error == "boom" and not result.available


def test_github_latest_reads_the_redirect_without_following_it(monkeypatch) -> None:
    seen = []

    def open_latest() -> str:
        seen.append(LATEST_URL)
        return "https://github.com/IamMrCupp/benchhud/releases/tag/v0.35.0"

    assert updates.github_latest(open_latest) == "0.35.0"
    assert seen == ["https://github.com/IamMrCupp/benchhud/releases/latest"]


def test_the_release_page_link() -> None:
    assert UpdateResult("0.34.2", "0.34.3", "github").url == (
        "https://github.com/IamMrCupp/benchhud/releases/tag/v0.34.3"
    )
    assert UpdateResult("0.34.2", None, "github", "x").url == (
        "https://github.com/IamMrCupp/benchhud/releases"
    )


def test_answer_for_each_outcome() -> None:
    newer = update_answer_html(UpdateResult("0.34.2", "0.34.3", "apt"))
    assert "0.34.3 is out" in newer and "apt upgrade" in newer
    assert "releases/tag/v0.34.3" in newer

    same = update_answer_html(UpdateResult("0.34.3", "0.34.3", "github"))
    assert "0.34.3 is the newest release" in same

    ahead = update_answer_html(UpdateResult("0.35.0", "0.34.3", "github"))
    assert "newer than the latest release (0.34.3)" in ahead

    failed = update_answer_html(UpdateResult("0.34.3", None, "github", "GitHub answered 503"))
    assert "Couldn't check for updates: GitHub answered 503" in failed


def test_answer_escapes_what_came_back() -> None:
    text = update_answer_html(UpdateResult("0.34.3", None, "github", "<b>nope</b>"))
    assert "<b>nope</b>" not in text and "&lt;b&gt;" in text


# The real lookups, taken before the suite's offline fixture swaps them out.
REAL_OPEN_LATEST = updates._open_latest
REAL_APT_POLICY = updates.apt_policy


@contextmanager
def _server(status: int, location: str | None = None):
    """A local HTTP server that answers every HEAD with ``status``."""

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self.send_response(status)
            if location is not None:
                self.send_header("Location", location)
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/IamMrCupp/benchhud/releases/latest"
    finally:
        server.shutdown()
        server.server_close()


def test_open_latest_reads_the_redirect_and_doesnt_follow_it() -> None:
    tag = "https://github.com/IamMrCupp/benchhud/releases/tag/v0.35.0"
    with _server(302, tag) as url:
        assert REAL_OPEN_LATEST(url) == tag


@pytest.mark.parametrize(("status", "said"), [(200, "200, not a redirect"), (404, "answered 404")])
def test_open_latest_without_a_redirect_is_an_error(status: int, said: str) -> None:
    with _server(status) as url, pytest.raises(UpdateCheckError, match=said):
        REAL_OPEN_LATEST(url)


def test_open_latest_with_nothing_listening_is_an_error() -> None:
    with _server(302) as url:
        pass  # closed again: nothing listens there now
    with pytest.raises(UpdateCheckError, match="couldn't reach GitHub"):
        REAL_OPEN_LATEST(url)


def test_tls_context_is_the_system_trust_store() -> None:
    import truststore

    context = updates.tls_context()
    assert isinstance(context, truststore.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_apt_policy_runs_apt_cache_in_the_c_locale(monkeypatch) -> None:
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, env=kwargs["env"], timeout=kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, POLICY_BEHIND, "")

    monkeypatch.setattr(subprocess, "run", run)

    assert REAL_APT_POLICY() == POLICY_BEHIND
    assert seen["argv"] == ["apt-cache", "policy", "benchhud"]
    assert seen["env"]["LC_ALL"] == "C"
    assert seen["timeout"] == updates.TIMEOUT_S


def test_apt_policy_is_none_when_apt_cache_fails_or_isnt_there(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 100, "", "E: nope")
    )
    assert REAL_APT_POLICY() is None

    def missing(argv, **kw):
        raise FileNotFoundError("apt-cache")

    monkeypatch.setattr(subprocess, "run", missing)
    assert REAL_APT_POLICY() is None


def test_trailing_zeros_dont_make_a_version_newer() -> None:
    assert version_key("0.35") == version_key("0.35.0") == (0, 35)
    assert not UpdateResult("0.35", "0.35.0", "github").available
    assert version_key("0") == (0,)


def test_the_suite_never_goes_online() -> None:
    result = check("0.34.2")
    assert result.latest is None and result.error == "tests don't go online"
