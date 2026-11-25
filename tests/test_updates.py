"""The update check's logic (#560): where it looks and what it makes of the answer."""

from __future__ import annotations

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
        ("0.13.0-1~ubuntu-resolute", (0, 13, 0)),
        ("1:2.0", (2, 0)),
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
