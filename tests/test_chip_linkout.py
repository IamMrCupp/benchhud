"""Tests for link-out addresses — marking sites and a document's own address.

Pure Python: nothing is opened and nothing is fetched. The marks are made up,
and so are the documents; their addresses are on example.com.
"""

from __future__ import annotations

import pytest

from benchhud.chips.linkout import (
    MARKING_SITES,
    MAX_SENT,
    MarkingSite,
    WebAddress,
    document_address,
    sendable,
    web_address,
)
from benchhud.chips.model import Document

_SITES = {site.host: site for site in MARKING_SITES}
_YOONEED = _SITES["smd.yooneed.one"]
_S_MANUALS = _SITES["www.s-manuals.com"]
_TI = _SITES["www.ti.com"]


def _url(site: MarkingSite, marking: str) -> str | None:
    address = site.address(marking)
    return None if address is None else address.url


# --- what may be sent ---------------------------------------------------------


def test_a_mark_is_sent_trimmed_the_way_it_is_matched() -> None:
    assert sendable("  X1=6A ") == "X1=6A"
    assert sendable("X1   4410") == "X1 4410"


@pytest.mark.parametrize(
    "typed",
    [None, "", "   ", "Ω1", "X1\x07", "X1​", "é2", "X" * (MAX_SENT + 1)],
)
def test_what_is_not_a_plain_mark_is_not_sent(typed) -> None:
    assert sendable(typed) is None
    assert [site.address(typed) for site in MARKING_SITES] == [None] * len(MARKING_SITES)


def test_the_longest_mark_that_is_sent() -> None:
    assert sendable("X" * MAX_SENT) == "X" * MAX_SENT


@pytest.mark.parametrize("typed", [42, b"X1", ["X1"], object()])
def test_what_is_not_text_is_not_sent(typed) -> None:
    assert sendable(typed) is None
    assert MARKING_SITES[0].address(typed) is None


def test_whitespace_of_any_kind_goes_as_one_space() -> None:
    # Trimmed the way a mark is matched, before anything is refused.
    assert sendable("X1\u2003\t4410") == "X1 4410"


# --- the sites ----------------------------------------------------------------


def test_the_built_in_sites() -> None:
    assert [site.host for site in MARKING_SITES] == [
        "smd.yooneed.one",
        "www.s-manuals.com",
        "www.ti.com",
    ]
    for site in MARKING_SITES:
        # What a click does is said in words that name the host, and say
        # what the site is told.
        assert site.host in site.does
        assert "told" in site.does
        assert site.cant


def test_the_codebook_that_only_answers_over_plain_http_is_left_out() -> None:
    assert not any("marsport" in site.host for site in MARKING_SITES)


@pytest.mark.parametrize(
    ("marking", "url"),
    [
        ("X1=6A", "https://smd.yooneed.one/search?code=X1%3D6A"),
        ("x1", "https://smd.yooneed.one/search?code=x1"),
        ("7", "https://smd.yooneed.one/search?code=7"),
        ("X1 4410", "https://smd.yooneed.one/search?code=X1%204410"),
        # Everything that means something in a URL goes as data, not as syntax.
        ("A&b=c#d?e/f%g+h", "https://smd.yooneed.one/search?code=A%26b%3Dc%23d%3Fe%2Ff%25g%2Bh"),
    ],
)
def test_yooneed_is_searched_for_the_whole_mark(marking, url) -> None:
    assert _url(_YOONEED, marking) == url


@pytest.mark.parametrize(
    ("marking", "url"),
    [
        ("X1=6A", "https://www.s-manuals.com/smd/x1"),  # the first two characters, lower case
        ("x1", "https://www.s-manuals.com/smd/x1"),
        ("7", "https://www.s-manuals.com/smd/7"),  # a one-character mark has a one-character page
        ("X 4410", "https://www.s-manuals.com/smd/x4"),  # a space isn't part of a page name
        ("-0A", "https://www.s-manuals.com/smd/dash0"),  # the site spells symbols out
        ("5.1", "https://www.s-manuals.com/smd/5dot"),
        ("A*", "https://www.s-manuals.com/smd/aasterisk"),
        ("**", "https://www.s-manuals.com/smd/asteriskasterisk"),
    ],
)
def test_s_manuals_is_opened_at_the_page_for_the_first_two_characters(marking, url) -> None:
    assert _url(_S_MANUALS, marking) == url


@pytest.mark.parametrize("marking", ["X=6A", "=5", "A+", "#1", "_Z"])
def test_s_manuals_has_no_page_for_a_character_it_cannot_name(marking) -> None:
    assert _S_MANUALS.address(marking) is None
    assert _YOONEED.address(marking) is not None  # the other sites still can


def test_s_manuals_is_never_told_more_than_two_characters() -> None:
    address = _S_MANUALS.address("X1=6A-SECRET")
    assert address is not None
    assert address.url == "https://www.s-manuals.com/smd/x1"


@pytest.mark.parametrize(
    ("marking", "url"),
    [
        ("X1=6A", "https://www.ti.com/packaging/en/docs/partlookup.tsp?partmarking=X1%3D6A"),
        ("X1", "https://www.ti.com/packaging/en/docs/partlookup.tsp?partmarking=X1"),
        ("X1 4410", "https://www.ti.com/packaging/en/docs/partlookup.tsp?partmarking=X1%204410"),
    ],
)
def test_ti_is_searched_for_the_whole_mark(marking, url) -> None:
    assert _url(_TI, marking) == url


def test_ti_needs_two_characters() -> None:
    assert _TI.address("7") is None
    assert "two characters" in _TI.cant


@pytest.mark.parametrize("marking", ["X1=6A", "7", "A&b=c#d", "x 1", "~!@$^*()", "X" * MAX_SENT])
def test_every_address_goes_to_the_site_the_button_names(marking) -> None:
    for site in MARKING_SITES:
        address = site.address(marking)
        if address is None:
            continue
        assert address.host == site.host
        assert address.url.startswith(f"https://{site.host}/")
        assert web_address(address.url) == address  # and is itself fit to open
        assert " " not in address.url


def test_a_site_whose_builder_strays_to_another_host_opens_nothing() -> None:
    stray = MarkingSite(
        host="marks.example.com",
        does="Opens marks.example.com.",
        cant="No page.",
        build=lambda mark: "https://elsewhere.example.com/?q=" + mark,
    )
    assert stray.address("X1") is None
    honest = MarkingSite(
        host="marks.example.com",
        does="Opens marks.example.com.",
        cant="No page.",
        build=lambda mark: "https://marks.example.com/?q=" + mark,
    )
    assert honest.address("X1") == WebAddress(
        "https://marks.example.com/?q=X1", "marks.example.com"
    )


# --- a document's own address -------------------------------------------------


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://www.example.com/parts/ex1234/datasheet", "www.example.com"),
        ("https://example.com", "example.com"),
        ("https://example.com/", "example.com"),
        ("https://docs.example.com/a/b.pdf?rev=C&lang=en#page=3", "docs.example.com"),
        ("https://example.com/a%20b/(1)~x_y,z;k=v@2", "example.com"),
        ("https://example.com/a%2Fb%3d", "example.com"),
        ("https://xn--bcher-kva.example.com/x", "xn--bcher-kva.example.com"),
        ("  https://example.com/x  ", "example.com"),  # stray space around it in the file
    ],
)
def test_a_plain_https_address_may_be_opened(url, host) -> None:
    address = web_address(url)
    assert address is not None
    assert address.host == host
    assert address.url == url.strip()


def test_the_host_is_shown_in_lower_case_and_opened_that_way() -> None:
    assert web_address("HTTPS://WWW.Example.COM/Parts/EX1234") == WebAddress(
        "https://www.example.com/Parts/EX1234", "www.example.com"
    )


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        42,
        "www.example.com/x",  # no scheme
        "http://www.example.com/x",  # in the clear
        "ftp://www.example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>1</script>",
        "https:/www.example.com/x",
        "https:///www.example.com/x",
        "https://",
        "https://user@www.example.com/x",  # a name before the host
        "https://www.example.com@evil.example.com/x",  # …made to look like the host
        "https://user:secret@www.example.com/x",
        "https://www.example.com:8443/x",  # a port
        "https://www.example.com:443/x",
        "https://192.168.1.1/admin",  # an address on someone's network
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://localhost/x",  # no dot: a machine, not a site
        "https://intranet/x",
        "https://www.example.com\\@evil.example.com/",
        "https://www.example.com/a b",  # a space inside
        "https://www.example.com/a\tb",
        "https://www.example.com/a\nb",
        "https://www.example.com/<b>x</b>",
        'https://www.example.com/"x"',
        "https://www.exämple.com/x",  # a look-alike host isn't shown or opened
        # Characters that fold to an ASCII letter when case is ignored: each
        # would be shown as typed here and opened as something else.
        "https://www.t\u0131.com/x",  # dotless i
        "https://www.T\u0130.com/x",  # capital I with a dot
        "https://example\u017f.com/x",  # long s
        "https://\u212aernel.example.com/x",  # the Kelvin sign
        "http\u017f://www.example.com/x",
        "https://\uff45xample.com/x",  # fullwidth e
        "https://example.com\u200b.evil.example.org/x",  # a zero-width space
        "https://ex\u0430mple.com/x",  # Cyrillic a
        "https://example.com./x",  # a trailing dot
        "https://2130706433/",  # an IP address written as one number
        "https://0x7f000001/",
        "https://127.1/",
        "https://example.com/%zz",  # a % that isn't the start of an escape
        "https://example.com/100%",
        "https://www.example.com/ü",
        "https://-bad.example.com/x",
        "https://bad-.example.com/x",
        "https://www.example.c/x",
        "https://www..example.com/x",
        "https://www.example.com/" + "a" * 2000,
    ],
)
def test_anything_else_is_not_opened(url) -> None:
    assert web_address(url) is None


def test_a_documents_address() -> None:
    listed = Document("datasheet", "EX1234.pdf", 14, url="https://www.example.com/ex1234")
    assert document_address(listed) == WebAddress(
        "https://www.example.com/ex1234", "www.example.com"
    )
    assert document_address(Document("datasheet", "EX1234.pdf")) is None
    assert document_address(Document("datasheet", "EX1234.pdf", url="http://example.com/x")) is None
