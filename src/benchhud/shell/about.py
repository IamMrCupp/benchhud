"""About-dialog content."""

from __future__ import annotations

from benchhud import __version__

REPO_URL = "https://github.com/IamMrCupp/benchhud"
LICENSE_NAME = "AGPL-3.0-or-later"
LICENSE_URL = "https://www.gnu.org/licenses/agpl-3.0.html"


def about_html() -> str:
    """Rich-text body for the About dialog."""
    return (
        f"<h3>benchhud {__version__}</h3>"
        "<p>A microsoldering / board-repair bench HUD — thermal-fused scope, "
        "instrument telemetry, and a virtual camera for OBS.</p>"
        f'<p><a href="{REPO_URL}">{REPO_URL.removeprefix("https://")}</a> · '
        f'<a href="{LICENSE_URL}">{LICENSE_NAME}</a></p>'
    )
