"""Hand an address to the operator's browser.

The same door the About box's links go through: Qt's desktop services, which
ask the OS to open the address in whatever the operator browses with.
benchhud itself makes no request — it never sees the page.

It doesn't take a caller's word for the address. Whatever it's given has to
pass :func:`benchhud.chips.linkout.web_address` — ``https://`` to a plain
ASCII host name, no ``user@``, no port, no IP address — and then has to mean
the same host to Qt's own parser. One that doesn't is refused with a log
line. Never raises into the HUD.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from benchhud.chips.linkout import web_address

log = logging.getLogger(__name__)

#: Asks the OS to open a URL; true when it took it.
Launcher = Callable[[QUrl], bool]


def open_in_browser(url: str, *, launch: Launcher | None = None) -> bool:
    """Open ``url`` in the operator's browser. False when it wasn't handed over."""
    try:
        address = web_address(url)
        if address is None:
            log.warning("browser: not opening %r — not a plain https address", url)
            return False
        target = QUrl(address.url, QUrl.ParsingMode.StrictMode)
        host = target.host(QUrl.ComponentFormattingOption.FullyEncoded)
        if not target.isValid() or target.scheme() != "https" or host != address.host:
            # Two parsers, one address: if they disagree about where it goes,
            # it goes nowhere.
            log.warning("browser: not opening %r — its host isn't what it looks like", url)
            return False
        return bool((launch or QDesktopServices.openUrl)(target))
    except Exception:
        log.exception("browser: could not open %r", url)
        return False
