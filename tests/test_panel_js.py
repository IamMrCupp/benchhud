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
    assert did["reloads"] == 1
    assert did["stored"] == {RELOADED_FOR: "new"}


def test_a_page_from_the_running_build_stays_put(page) -> None:
    """The ordinary restart: same benchhud, same pages, nothing to blink."""
    did = page(loaded="same", steps=[{"build": "same"}, {"error": True}, {"build": "same"}])
    assert did["reloads"] == 0
    assert did["fetches"] == []


def test_it_asks_for_the_page_before_reloading(page) -> None:
    """The exact address, query and all, and never from a cache."""
    did = page(loaded="old", steps=[{"build": "new"}])
    assert did["fetches"] == [{"url": PAGE, "options": {"cache": "no-store"}}]


@pytest.mark.parametrize("outcome", ["notok", "reject", "hang"])
def test_no_reload_when_the_page_cant_be_fetched(page, outcome) -> None:
    """benchhud went away again, or the address is refused: an error page would
    have no script on it and would never come back by itself."""
    did = page(loaded="old", fetch=[outcome], steps=[{"build": "new"}])
    assert did["reloads"] == 0
    assert did["stored"] == {}


@pytest.mark.parametrize("outcome", ["notok", "reject"])
def test_a_failed_check_is_tried_again_on_the_next_connection(page, outcome) -> None:
    did = page(
        loaded="old",
        fetch=[outcome, "ok"],
        steps=[{"build": "new"}, {"error": True}, {"build": "new"}],
    )
    assert len(did["fetches"]) == 2
    assert did["reloads"] == 1


def test_one_reload_per_build(page) -> None:
    """After a reload that came back with the old page, it doesn't loop."""
    did = page(loaded="old", stored={RELOADED_FOR: "new"}, steps=[{"build": "new"}])
    assert did["reloads"] == 0
    assert did["fetches"] == []


def test_another_pages_reload_doesnt_count_as_this_ones(page) -> None:
    """Pages in one tab share storage. Seen in a real browser: the terminal page
    stayed stale because the job card had already reloaded for the same build."""
    other = {"benchhud-reloaded-for:/terminal": "new", "benchhud-reloaded-for": "new"}
    did = page(loaded="old", stored=other, steps=[{"build": "new"}])
    assert did["reloads"] == 1
    assert did["stored"] == {**other, RELOADED_FOR: "new"}


def test_a_later_build_reloads_again(page) -> None:
    did = page(loaded="old", stored={RELOADED_FOR: "new"}, steps=[{"build": "newer"}])
    assert did["reloads"] == 1
    assert did["stored"] == {RELOADED_FOR: "newer"}


def test_two_announcements_at_once_are_one_check(page) -> None:
    did = page(
        loaded="old",
        fetch=["hang"],
        steps=[{"build": "new", "together": True}, {"build": "new"}],
    )
    assert len(did["fetches"]) == 1


def test_it_still_reloads_without_session_storage(page) -> None:
    did = page(loaded="old", storage="throws", steps=[{"build": "new"}])
    assert did["reloads"] == 1


@pytest.mark.parametrize(
    ("loaded", "build"),
    [(None, "new"), ("", "new"), ("old", "")],
    ids=["page not stamped", "empty stamp", "empty announcement"],
)
def test_nothing_to_compare_means_no_reload(page, loaded, build) -> None:
    did = page(loaded=loaded, steps=[{"build": build}])
    assert did["reloads"] == 0
    assert did["fetches"] == []


def test_the_build_event_is_not_handed_to_the_panel(page) -> None:
    """The panel draws data; the build is the page's own business."""
    did = page(loaded="same", steps=[{"build": "same"}, {"message": {"job": {"id": 7}}}])
    assert did["renders"] == [None, {"job": {"id": 7}}]


def test_the_feed_still_works_as_before(page) -> None:
    did = page(loaded="same", steps=[{"message": {"n": 1}}, {"error": True}])
    assert did["sources"] == ["/events/job"]
    assert did["renders"] == [None, {"n": 1}, None]
    assert did["offline"] is True
