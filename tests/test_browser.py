"""Tests for handing an address to the browser. Nothing is opened: the launcher is a fake."""

from __future__ import annotations

import pytest

from benchhud.shell.browser import open_in_browser


class _Launcher:
    def __init__(self, answer: object = True) -> None:
        self.answer = answer
        self.urls: list[str] = []

    def __call__(self, url) -> bool:
        self.urls.append(url.toString())
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_an_https_address_is_handed_over() -> None:
    launch = _Launcher()
    assert open_in_browser("https://www.example.com/a?b=c%3D1#d", launch=launch) is True
    assert launch.urls == ["https://www.example.com/a?b=c%3D1#d"]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://www.example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "www.example.com",
        "https://",
        "https://www.example.com\\@evil.example.com/",
        "https://exa mple.com/",
        "https://user@www.example.com/",
        "https://www.example.com:8443/",
    ],
)
def test_anything_else_is_refused_before_the_os_is_asked(url) -> None:
    launch = _Launcher()
    assert open_in_browser(url, launch=launch) is False
    assert launch.urls == []


def test_an_os_that_will_not_take_it_is_a_no() -> None:
    assert open_in_browser("https://www.example.com/", launch=_Launcher(False)) is False


def test_a_launcher_that_raises_is_a_no_not_a_crash(caplog) -> None:
    assert open_in_browser("https://www.example.com/", launch=_Launcher(OSError("no"))) is False
    assert "could not open" in caplog.text
