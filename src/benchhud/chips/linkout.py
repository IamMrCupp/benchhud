"""Link-outs — where to look a mark up when your own chip data doesn't have it.

Two kinds, and both only ever build an address. Nothing here opens one and
nothing here touches the network: the caller hands the address to the
operator's browser, on an explicit click.

**A typed mark, on a marking site.** The sites are a short built-in list.
Each has no licence and no export, so its data can't be copied into chip data
— but a link is just a link. For each one the address was worked out from the
site's own pages (2026-11-22 UTC):

- ``smd.yooneed.one`` — its search form is ``GET /search?code=…``, which
  redirects to the page for the mark's first two characters, at that mark.
  Told: the whole mark.
- ``www.s-manuals.com`` — no search, one page per first two characters:
  ``/smd/1a`` for ``1AM``, lower case, with ``*``, ``-`` and ``.`` spelled out
  (``/smd/dash0``). A prefix with no page of its own gets the site's index.
  Told: the mark's first two characters (spaces aside), and nothing more.
- ``www.ti.com`` — TI's part marking lookup; its own Lookup button goes to
  ``partlookup.tsp?partmarking=…``. It wants two characters or more, and it
  only knows TI parts. Told: the whole mark.

Left out: the SMD Codebook (``marsport.org.uk``). It answers over plain
``http`` only — its ``https`` doesn't complete a handshake — and a mark isn't
sent in the clear.

**A document's own address.** A chip's document can say where its maker
publishes it (``url``). That comes from a data file someone else may have
written, so it's held to a narrow shape before it's offered: ``https://``, a
plain ASCII host name with a dot in it, and nothing else in front of the path
— no ``user@``, no port, no IP address, no bare machine name, no character
outside ASCII that could pass for one inside it. Whatever doesn't fit is not
opened. It's the name's shape that's checked, not where it resolves:
``printer.local`` is a name with a dot in it, and passes.

**A link-out tells a third party what you're looking up.** So what's sent is
kept to the mark itself (or to the document's own address), it's percent-
encoded, and text that — once trimmed the way a mark is — isn't plain
printable ASCII, or is longer than any mark or part number, builds no address
at all.

Pure functions and data, no Qt, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from benchhud.chips.marking import normalise_mark
from benchhud.chips.model import Document

#: Longest text a link-out will send. A code is 16 characters at most and a
#: whole mark or a part number not much more; past this it's a stray paste.
MAX_SENT = 40

_HOST = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
# https, a host name, then at most a path, query and fragment made of the
# characters a URL may be written with (RFC 3986), a ``%`` only as the start
# of an escape. Matching the whole string leaves no room for ``user@``, a
# port, a backslash or a space — the things two URL parsers can disagree
# about. ``re.ASCII`` matters: without it, ignoring case lets ``[a-z]`` match
# a dotless i (U+0131), a long s (U+017F) and the Kelvin sign (U+212A) — a
# host shown as one name and opened as another.
_HTTPS_URL = re.compile(
    rf"(?i:https)://((?i:{_HOST}))"
    r"(?:[/?#](?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=]|%[0-9A-Fa-f]{2})*)?",
    re.ASCII,
)
_MAX_URL = 2000


@dataclass(frozen=True)
class WebAddress:
    """An address safe to hand to a browser, and the host it goes to."""

    url: str
    host: str


def web_address(url: object) -> WebAddress | None:
    """``url`` as an address that may be opened, or ``None`` if it isn't one.

    Only ``https://`` to a plain host name, written out in full — see the
    module docstring for what's refused and why. The host comes back in lower
    case: it's what the operator is shown before they click.
    """
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text or len(text) > _MAX_URL or not text.isascii():
        return None
    match = _HTTPS_URL.fullmatch(text)
    if match is None:
        return None
    host = match.group(1).lower()
    # The scheme and host are case-blind, so they go out as they're shown.
    return WebAddress(url="https://" + host + text[match.end(1) :], host=host)


def document_address(document: Document) -> WebAddress | None:
    """Where the document's maker publishes it, if it says and it's safe to open."""
    return web_address(getattr(document, "url", None))


def sendable(marking: str | None) -> str | None:
    """The mark as a link-out may send it, or ``None`` when it may not.

    Trimmed the way a mark is matched first — ends dropped, any run of
    whitespace as one space. Refused after that: nothing typed, anything that
    isn't printable ASCII (a mark is; a line of prose in another script is a
    paste gone wrong), and anything over :data:`MAX_SENT` characters.
    """
    if marking is not None and not isinstance(marking, str):
        return None
    text = normalise_mark(marking)
    if not text or len(text) > MAX_SENT:
        return None
    if not all(" " <= ch <= "~" for ch in text):
        return None
    return text


def _yooneed(mark: str) -> str | None:
    return "https://smd.yooneed.one/search?code=" + quote(mark, safe="")


_S_MANUALS_SPELLED = {"*": "asterisk", "-": "dash", ".": "dot"}


def _s_manuals(mark: str) -> str | None:
    spelled = []
    for ch in mark.replace(" ", "")[:2]:
        if ch.isalnum():
            spelled.append(ch.lower())
        elif ch in _S_MANUALS_SPELLED:
            spelled.append(_S_MANUALS_SPELLED[ch])
        else:
            return None  # the site has no page name for this character
    return "https://www.s-manuals.com/smd/" + "".join(spelled) if spelled else None


def _ti(mark: str) -> str | None:
    if len(mark) < 2:
        return None  # the site's own rule for a marking search
    return "https://www.ti.com/packaging/en/docs/partlookup.tsp?partmarking=" + quote(mark, safe="")


@dataclass(frozen=True)
class MarkingSite:
    """One site a mark can be looked up on.

    ``host`` is where the browser goes — it's what the button says. ``does``
    says what a click does and what the site is told, and ``cant`` why there
    is nothing to open for some marks; both are this module's own words,
    safe to show anywhere. ``build`` turns a sendable mark into the address,
    or ``None`` when the site has no page for it.
    """

    host: str
    does: str
    cant: str
    build: Callable[[str], str | None]

    def address(self, marking: str | None) -> WebAddress | None:
        """The address for ``marking`` on this site, or ``None`` if there isn't one."""
        mark = sendable(marking)
        if mark is None:
            return None
        found = web_address(self.build(mark))
        # A site's builder only ever goes to that site. Held to it here, so a
        # slip in one can't send a mark somewhere the button doesn't say.
        return found if found is not None and found.host == self.host else None


#: The marking sites, in the order they're offered.
MARKING_SITES: tuple[MarkingSite, ...] = (
    MarkingSite(
        host="smd.yooneed.one",
        does=(
            "Opens smd.yooneed.one in your browser and searches it for what's typed. "
            "That site is told the whole mark."
        ),
        cant="smd.yooneed.one can't be searched for this.",
        build=_yooneed,
    ),
    MarkingSite(
        host="www.s-manuals.com",
        does=(
            "Opens www.s-manuals.com in your browser, at its page for marks that start "
            "the same way. That site is told the mark's first two characters only."
        ),
        cant="www.s-manuals.com has no page for a mark that starts like this one.",
        build=_s_manuals,
    ),
    MarkingSite(
        host="www.ti.com",
        does=(
            "Opens www.ti.com in your browser, at TI's part marking lookup, searching for "
            "what's typed. It only knows TI parts. That site is told the whole mark."
        ),
        cant="TI's part marking lookup needs at least two characters.",
        build=_ti,
    ),
)
