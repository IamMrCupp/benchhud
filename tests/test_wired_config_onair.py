"""A bench-setup-wired config keeps every on-air default (bench-suite plan, todo 10).

``bench-setup`` points benchhud at intake, bench-parts and bench-recon through
``benchhud config set``. Wiring the services must change only the connection
settings: the recon panels stay tagged sensitive, and nothing that publishes
to a stream (virtual cam, browser sources, the terminal share) turns on.
"""

from __future__ import annotations

from benchhud import config as config_module
from benchhud.cli_config import set_value
from benchhud.recon.http_source import HttpReconSource
from benchhud.shell.main_window import MainWindow

# What bench-setup writes on a same-host install (ports from the plan).
WIRING = {
    "intake.base_url": "http://127.0.0.1:8401",
    "intake.token": "intake-token",
    "inventory.base_url": "http://127.0.0.1:8402",
    "inventory.token": "parts-token",
    "recon.base_url": "http://127.0.0.1:8403",
    "recon.token": "recon-token",
}

#: Settings that put something on a stream. Wiring must not touch any of them.
ON_AIR_SETTINGS = (
    ("output", "enabled"),
    ("browser_sources", "enabled"),
    ("terminal", "enabled"),
    ("terminal", "share"),
    ("telemetry_overlay", "enabled"),
    ("graph_overlay", "enabled"),
    ("now_playing", "enabled"),
)


def _wired(tmp_path) -> config_module.Config:
    path = tmp_path / "config.toml"
    for key, value in WIRING.items():
        set_value(key, value, str(path))
    return config_module.load(path)


def test_wiring_sets_only_the_connections(tmp_path) -> None:
    wired = _wired(tmp_path)
    assert wired.recon.base_url == "http://127.0.0.1:8403"
    defaults = config_module.Config()
    for section, name in ON_AIR_SETTINGS:
        assert getattr(getattr(wired, section), name) == getattr(
            getattr(defaults, section), name
        ), f"wiring changed {section}.{name}"


def test_a_wired_window_keeps_recon_panels_off_air(qtbot, tmp_path) -> None:
    window = MainWindow(_wired(tmp_path))
    qtbot.addWidget(window)

    # The wiring took: recon is live, not the standalone stub.
    assert isinstance(window._recon_source, HttpReconSource)
    # And its surfaces are still tagged sensitive, so the compositor drops them.
    assert window.engagement_panel.sensitive is True
    assert window.capture_panel.sensitive is True
