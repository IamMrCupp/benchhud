"""Hand an address to the operator's browser.

The same door the About box's links go through: Qt's desktop services, which
ask the OS to open the address in whatever the operator browses with.
benchhud itself makes no request — it never sees the page.

Only ``https://`` addresses go through it, parsed strictly, so a caller that
forgot to check gets a refusal and a log line rather than a surprise. Never
raises into the HUD.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)

#: Asks the OS to open a URL; true when it took it.
Launcher = Callable[[QUrl], bool]


def open_in_browser(url: str, *, launch: Launcher | None = None) -> bool:
    """Open ``url`` in the operator's browser. False when it wasn't handed over."""
    try:
        target = QUrl(url, QUrl.ParsingMode.StrictMode)
        if not target.isValid() or target.scheme() != "https" or not target.host():
            log.warning("browser: not opening %r — not a plain https address", url)
            return False
        if target.userName() or target.password() or target.port() != -1:
            log.warning("browser: not opening %r — it names a user or a port", url)
            return False
        return bool((launch or QDesktopServices.openUrl)(target))
    except Exception:
        log.exception("browser: could not open %r", url)
        return False
