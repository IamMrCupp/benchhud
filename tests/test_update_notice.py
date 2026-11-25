"""The update notice in the window (#560): status bar, Help menu, and when it checks."""

from __future__ import annotations

import threading

import pytest

from benchhud import __version__
from benchhud.config import Config
from benchhud.shell import main_window as main_window_module
from benchhud.shell.main_window import MainWindow
from benchhud.updates import UpdateResult


def _window(qtbot, result: UpdateResult | Exception, config: Config | None = None) -> MainWindow:
    window = MainWindow(config or Config())
    qtbot.addWidget(window)
    calls = []

    def fake_check() -> UpdateResult:
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    window.update_check = fake_check
    window.check_calls = calls
    return window


def _finish(qtbot, window: MainWindow) -> None:
    qtbot.waitUntil(lambda: not window._update_checking, timeout=2000)


def test_help_menu_has_check_for_updates(qtbot) -> None:
    window = _window(qtbot, UpdateResult(__version__, __version__, "github"))
    menus = [a.text() for a in window.menuBar().actions()]
    assert "Help" in menus
    assert window.check_updates_action.text() == "Check for updates…"


def test_no_notice_until_a_check_finds_something(qtbot) -> None:
    window = _window(qtbot, UpdateResult(__version__, __version__, "github"))
    assert window.update_label.isHidden()


def test_a_newer_release_shows_in_the_status_bar(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))

    window._start_update_check(asked=False)
    _finish(qtbot, window)

    assert not window.update_label.isHidden()
    assert "0.34.3 is out" in window.update_label.text()
    assert "releases/tag/v0.34.3" in window.update_label.text()
    assert window.update_label.openExternalLinks()
    assert window.update_box is None  # nobody asked, so no dialog


def test_the_notice_goes_once_up_to_date(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))
    window._start_update_check(asked=False)
    _finish(qtbot, window)

    window.update_check = lambda: UpdateResult("0.34.3", "0.34.3", "github")
    window._start_update_check(asked=False)
    _finish(qtbot, window)

    assert window.update_label.isHidden()


def test_a_failed_check_on_the_timer_is_quiet_and_keeps_the_notice(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))
    window._start_update_check(asked=False)
    _finish(qtbot, window)

    window.update_check = lambda: UpdateResult("0.34.2", None, "github", "offline")
    window._start_update_check(asked=False)
    _finish(qtbot, window)

    assert not window.update_label.isHidden()
    assert window.update_box is None


def test_asking_says_what_it_found(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "apt"))

    window.check_updates_action.trigger()
    _finish(qtbot, window)

    assert window.update_box is not None
    assert "0.34.3 is out" in window.update_box.text()
    assert "apt upgrade" in window.update_box.text()
    window.update_box.close()


def test_asking_says_so_when_it_cant_check(qtbot) -> None:
    window = _window(qtbot, UpdateResult(__version__, None, "github", "couldn't reach GitHub"))

    window.check_for_updates()
    _finish(qtbot, window)

    assert "Couldn't check for updates: couldn't reach GitHub" in window.update_box.text()
    window.update_box.close()


def test_a_check_that_raises_doesnt_take_the_window_down(qtbot) -> None:
    window = _window(qtbot, RuntimeError("boom"))

    window.check_for_updates()
    _finish(qtbot, window)

    assert "boom" in window.update_box.text()
    window.update_box.close()


def test_asking_while_a_check_runs_gets_that_checks_answer(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))
    window._update_checking = True  # one is already out

    window.check_for_updates()
    assert window.check_calls == []  # no second check

    window._on_update_checked(UpdateResult("0.34.2", "0.34.3", "github"))
    assert window.update_box is not None
    window.update_box.close()


def test_start_update_checks_schedules_them(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "UPDATE_FIRST_CHECK_MS", 10)
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))

    window.start_update_checks()
    window.start_update_checks()  # twice is the same as once

    qtbot.waitUntil(lambda: not window.update_label.isHidden(), timeout=2000)
    assert window.check_calls == [1]
    assert window._update_timer is not None and window._update_timer.isActive()
    assert window._update_timer.interval() == main_window_module.UPDATE_CHECK_INTERVAL_MS
    assert window.update_box is None


def test_check_false_means_it_never_checks_by_itself(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "UPDATE_FIRST_CHECK_MS", 10)
    config = Config()
    config.updates.check = False
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"), config)

    window.start_update_checks()
    qtbot.wait(100)

    assert window._update_timer is None
    assert window.check_calls == []


def test_check_false_still_answers_help(qtbot) -> None:
    config = Config()
    config.updates.check = False
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"), config)

    window.check_for_updates()
    _finish(qtbot, window)

    assert "0.34.3 is out" in window.update_box.text()
    window.update_box.close()


@pytest.mark.parametrize("raw", [{"updates": {"check": False}}, {"updates": {"check": 0}}])
def test_config_reads_check(raw) -> None:
    assert Config.from_dict(raw).updates.check is False
    assert Config.from_dict({}).updates.check is True


def test_help_says_its_checking_while_it_checks(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))
    gate = threading.Event()
    window.update_check = lambda: (gate.wait(2), UpdateResult("0.34.2", "0.34.3", "github"))[1]

    window.check_for_updates()
    assert window.check_updates_action.text() == "Checking for updates…"
    assert not window.check_updates_action.isEnabled()

    gate.set()
    _finish(qtbot, window)
    assert window.check_updates_action.text() == "Check for updates…"
    assert window.check_updates_action.isEnabled()
    window.update_box.close()


def test_a_timer_check_doesnt_touch_the_menu(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))
    gate = threading.Event()
    window.update_check = lambda: (gate.wait(2), UpdateResult("0.34.2", "0.34.3", "github"))[1]

    window._start_update_check(asked=False)
    assert window.check_updates_action.isEnabled()
    gate.set()
    _finish(qtbot, window)


def test_asking_again_replaces_the_answer(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.3", "0.34.3", "github"))
    window.check_for_updates()
    _finish(qtbot, window)
    first = window.update_box
    gone = []
    first.destroyed.connect(lambda: gone.append(1))

    window.check_for_updates()
    _finish(qtbot, window)

    assert window.update_box is not first
    qtbot.waitUntil(lambda: gone == [1], timeout=2000)
    window.update_box.close()


def test_the_notice_escapes_what_came_back(qtbot) -> None:
    # Its upstream part is 1.0.0, so it counts as newer and the label shows.
    window = _window(qtbot, UpdateResult("0.34.2", "1.0.0-<i>", "github"))

    window._start_update_check(asked=False)
    _finish(qtbot, window)

    assert not window.update_label.isHidden()
    assert "<i>" not in window.update_label.text()
    assert "1.0.0-&lt;i&gt; is out" in window.update_label.text()


def test_asking_during_a_timer_check_says_its_checking(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))
    gate = threading.Event()
    window.update_check = lambda: (gate.wait(2), UpdateResult("0.34.2", "0.34.3", "github"))[1]
    window._start_update_check(asked=False)

    window.check_for_updates()
    assert window.check_updates_action.text() == "Checking for updates…"

    gate.set()
    _finish(qtbot, window)
    assert window.check_updates_action.text() == "Check for updates…"
    assert window.update_box is not None
    window.update_box.close()


def test_closing_the_window_stops_the_checks(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "0.34.3", "github"))
    window.start_update_checks()

    window.close()

    assert not window._update_timer.isActive()
    assert not window._update_first.isActive()


def test_a_latest_that_isnt_a_version_shows_nothing(qtbot) -> None:
    window = _window(qtbot, UpdateResult("0.34.2", "1.0.0&<i>", "github"))

    window._start_update_check(asked=False)
    _finish(qtbot, window)

    assert window.update_label.isHidden()


def test_the_window_check_goes_through_the_suites_offline_lookups(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    window.check_for_updates()
    _finish(qtbot, window)

    assert "tests don't go online" in window.update_box.text()
    window.update_box.close()
