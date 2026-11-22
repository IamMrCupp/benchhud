"""The browser-source pages' own script, run for real (#501).

``panel.js`` decides when a page left open in OBS reloads itself. Getting that
wrong shows on stream either way: a page that never reloads keeps an old layout
after an upgrade, and one that reloads when it shouldn't blinks or, worse, lands
on an error page that never comes back. So the real file runs here, in Node,
against stand-ins for the browser (``panel_harness.js``).

Node comes with every GitHub runner. Without it the tests skip on a laptop and
fail in CI, so they can't go missing unnoticed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("panel_harness.js")
RELOADED_FOR = "benchhud-reloaded-for:/panel/job"
PAGE = "http://127.0.0.1:8780/panel/job?credit=0"


@pytest.fixture(scope="module")
def node() -> str:
    found = shutil.which("node")
    if found is None:
        if os.environ.get("CI"):
            pytest.fail("node is missing: the pages' script can't be tested")
        pytest.skip("node is not installed")
    return found


@pytest.fixture
def page(node, tmp_path):
    """Run panel.js through a scenario; returns what the page did."""
    script = files("benchhud.resources").joinpath("web", "panel.js")

    def run(**scenario) -> dict:
        scenario_file = tmp_path / "scenario.json"
        scenario_file.write_text(json.dumps(scenario))
        done = subprocess.run(
            [node, str(HARNESS), str(script), str(scenario_file)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    return run


def test_a_page_from_an_older_build_reloads(page) -> None:
    did = page(loaded="old", steps=[{"build": "new"}])
    assert len(did["reloads"]) == 1
    assert did["stored"] == {RELOADED_FOR: "new"}


def test_a_page_from_the_running_build_stays_put(page) -> None:
    """The ordinary restart: same benchhud, same pages, nothing to blink."""
    did = page(loaded="same", steps=[{"build": "same"}, {"error": True}, {"build": "same"}])
    assert did["reloads"] == []
    assert did["fetches"] == []
    assert did["closed"] == 0 and did["feeds"] == ["/events/job"]


def test_it_asks_for_the_page_before_reloading(page) -> None:
    """The exact address, query and all, and never from a cache."""
    did = page(loaded="old", steps=[{"build": "new"}])
    assert [(f["url"], f["cache"]) for f in did["fetches"]] == [(PAGE, "no-store")]


def test_it_gives_up_its_feed_before_asking(page) -> None:
    """A browser allows six connections to one host and every feed holds one.

    Found in review, in Chrome, with six pages open: the check and the reload
    each needed a seventh, so pages never reloaded, and once a queued reload
    landed after benchhud had stopped and left an error page. So the page lets
    go of its own connection first, and reads the answer to the end so the
    reload has it too.
    """
    did = page(loaded="old", steps=[{"build": "new"}])
    assert did["fetches"][0]["openFeeds"] == 0
    assert did["fetches"][0]["bodyRead"] is True
    assert did["reloads"] == [{"at": 0, "openFeeds": 0}]


def test_the_page_blanks_while_its_feed_is_given_up(page) -> None:
    """What was live a moment ago mustn't sit there looking live."""
    did = page(
        loaded="old",
        fetch=["hang"],
        steps=[{"message": {"n": 1}}, {"build": "new"}],
    )
    assert did["renders"] == [None, {"n": 1}, None]
    assert did["offline"] is True


@pytest.mark.parametrize("outcome", ["notok", "reject", "hang"])
def test_no_reload_when_the_page_cant_be_fetched(page, outcome) -> None:
    """benchhud went away again, or the address is refused: an error page would
    have no script on it and would never come back by itself."""
    did = page(loaded="old", fetch=[outcome], steps=[{"build": "new"}, {"advance": 60000}])
    assert did["reloads"] == []
    assert did["stored"] == {}
    assert did["openFeeds"] == 1  # and it's listening again


def test_no_answer_means_the_feed_comes_back_after_a_wait_and_it_asks_again(page) -> None:
    did = page(
        loaded="old",
        fetch=["reject", "ok"],
        steps=[{"build": "new"}, {"advance": 4999}],
    )
    assert did["feeds"] == ["/events/job"] and did["openFeeds"] == 0  # still waiting

    did = page(
        loaded="old",
        fetch=["reject", "ok"],
        steps=[{"build": "new"}, {"advance": 5000}, {"build": "new"}],
    )
    assert did["feeds"] == ["/events/job", "/events/job"]
    assert len(did["fetches"]) == 2
    assert len(did["reloads"]) == 1


def test_a_check_that_never_answers_is_given_up_on(page) -> None:
    """Otherwise the page would sit with no feed, blank, for good."""
    did = page(loaded="old", fetch=["hang"], steps=[{"build": "new"}, {"advance": 9999}])
    assert did["fetches"][0]["aborted"] is False and did["openFeeds"] == 0

    did = page(
        loaded="old",
        fetch=["hang", "ok"],
        steps=[{"build": "new"}, {"advance": 10000}, {"advance": 5000}, {"build": "new"}],
    )
    assert did["fetches"][0]["aborted"] is True
    assert did["feeds"] == ["/events/job", "/events/job"]
    assert len(did["reloads"]) == 1


def test_a_refusal_brings_the_feed_straight_back_and_isnt_asked_again(page) -> None:
    """The server answered, and said no. Asking every few seconds would keep the
    page blank for nothing; it goes back to showing live data on the old page."""
    did = page(
        loaded="old",
        fetch=["notok", "ok"],
        steps=[
            {"build": "new"},
            {"build": "new"},
            {"message": {"n": 2}},
            {"error": True},
            {"build": "new"},
        ],
    )
    assert did["feeds"] == ["/events/job", "/events/job"]
    assert len(did["fetches"]) == 1
    assert did["reloads"] == []
    assert {"n": 2} in did["renders"]
    assert did["pendingTimers"] == 0


def test_a_refusal_for_one_build_doesnt_cover_the_next(page) -> None:
    did = page(
        loaded="old",
        fetch=["notok", "ok"],
        steps=[{"build": "new"}, {"build": "newer"}],
    )
    assert len(did["reloads"]) == 1
    assert did["stored"] == {RELOADED_FOR: "newer"}


def test_the_timeout_is_cleared_once_the_page_answers(page) -> None:
    did = page(loaded="old", fetch=["notok"], steps=[{"build": "new"}])
    assert did["pendingTimers"] == 0


def test_one_reload_per_build(page) -> None:
    """After a reload that came back with the old page, it doesn't loop, and it
    keeps its feed: a stale page that's live beats a blank one."""
    did = page(loaded="old", stored={RELOADED_FOR: "new"}, steps=[{"build": "new"}])
    assert did["reloads"] == []
    assert did["fetches"] == []
    assert did["closed"] == 0


def test_another_pages_reload_doesnt_count_as_this_ones(page) -> None:
    """Pages in one tab share storage. Seen in a real browser: the terminal page
    stayed stale because the job card had already reloaded for the same build."""
    other = {"benchhud-reloaded-for:/terminal": "new", "benchhud-reloaded-for": "new"}
    did = page(loaded="old", stored=other, steps=[{"build": "new"}])
    assert len(did["reloads"]) == 1
    assert did["stored"] == {**other, RELOADED_FOR: "new"}


def test_a_later_build_reloads_again(page) -> None:
    did = page(loaded="old", stored={RELOADED_FOR: "new"}, steps=[{"build": "newer"}])
    assert len(did["reloads"]) == 1
    assert did["stored"] == {RELOADED_FOR: "newer"}


def test_a_second_announcement_on_a_given_up_feed_is_not_a_second_check(page) -> None:
    did = page(
        loaded="old",
        fetch=["hang"],
        steps=[{"build": "new", "together": True}, {"build": "new"}],
    )
    assert len(did["fetches"]) == 1


def test_it_still_reloads_without_session_storage(page) -> None:
    did = page(loaded="old", storage="throws", steps=[{"build": "new"}])
    assert len(did["reloads"]) == 1


@pytest.mark.parametrize(
    ("loaded", "build"),
    [(None, "new"), ("", "new"), ("old", "")],
    ids=["page not stamped", "empty stamp", "empty announcement"],
)
def test_nothing_to_compare_means_no_reload(page, loaded, build) -> None:
    did = page(loaded=loaded, steps=[{"build": build}])
    assert did["reloads"] == []
    assert did["fetches"] == []
    assert did["closed"] == 0


def test_the_build_event_is_not_handed_to_the_panel(page) -> None:
    """The panel draws data; the build is the page's own business."""
    did = page(loaded="same", steps=[{"build": "same"}, {"message": {"job": {"id": 7}}}])
    assert did["renders"] == [None, {"job": {"id": 7}}]


def test_the_feed_still_works_as_before(page) -> None:
    did = page(loaded="same", steps=[{"message": {"n": 1}}, {"error": True}])
    assert did["feeds"] == ["/events/job"]
    assert did["renders"] == [None, {"n": 1}, None]
    assert did["offline"] is True
