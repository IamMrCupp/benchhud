"""Serve HUD panels to OBS as Browser sources (#180).

One virtual camera is one picture, so it can't give OBS a separate source per
panel. A Browser source can, on every OS, with no driver: benchhud runs a small
stdlib HTTP server on loopback, one page per data panel, each updated live over
Server-Sent Events from the same readings that feed the Qt docks.

* :mod:`~benchhud.browser_sources.hub` — the latest payload per topic, shared
  between the GUI thread (publisher) and the server's threads (readers).
* :mod:`~benchhud.browser_sources.payloads` — what each page receives. The job
  card is a whitelist, so ``customer`` can't reach a stream by accident.
* :mod:`~benchhud.browser_sources.server` — the HTTP server itself.
"""

from benchhud.browser_sources.hub import TopicHub
from benchhud.browser_sources.server import PANELS, BrowserSourceServer

__all__ = ["PANELS", "BrowserSourceServer", "TopicHub"]
