"""About-dialog content."""

from __future__ import annotations

from benchhud import __version__

REPO_URL = "https://github.com/IamMrCupp/benchhud"


def about_html() -> str:
    """Rich-text body for the About dialog."""
    return (
        f"<h3>benchhud {__version__}</h3>"
        "<p>A microsoldering / board-repair bench HUD — thermal-fused scope, "
        "instrument telemetry, and a virtual camera for OBS.</p>"
        f'<p><a href="{REPO_URL}">{REPO_URL.removeprefix("https://")}</a> · MIT</p>'
    )
