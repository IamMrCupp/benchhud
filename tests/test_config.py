"""Tests for the config model and TOML persistence."""

from __future__ import annotations

import tomllib

import pytest

from benchhud import config as cfg


def test_defaults() -> None:
    c = cfg.Config()
    assert c.shell.window_width == 1200
    assert c.shell.logging.level == "INFO"
    assert c.panels.scope.device == "/dev/video4"
    assert c.output.device == "/dev/video10"
    assert c.faults.auto_log is False
    assert c.faults.current_threshold_a == 1.0
    assert c.panels.telemetry.baud == 115200
    assert c.tools == []


def test_round_trip(tmp_path) -> None:
    c = cfg.Config()
    c.shell.window_width = 1024
    c.shell.window_height = 768
    c.shell.logging.level = "DEBUG"
    c.output.enabled = True
    c.output.device = "/dev/video11"
    c.faults.auto_log = True
    c.faults.current_threshold_a = 2.5
    c.panels.thermal.enabled = True
    c.tools = [cfg.ToolConfig(name="OpenBoardView", command="openboardview")]

    path = tmp_path / "config.toml"
    cfg.save(c, path)
    loaded = cfg.load(path)

    assert loaded == c


def test_load_missing_returns_defaults(tmp_path) -> None:
    loaded = cfg.load(tmp_path / "does-not-exist.toml")
    assert loaded == cfg.Config()


def test_save_creates_parent_dirs(tmp_path) -> None:
    path = tmp_path / "nested" / "deep" / "config.toml"
    cfg.save(cfg.Config(), path)
    assert path.exists()


def test_layout_none_is_omitted_from_toml(tmp_path) -> None:
    path = tmp_path / "config.toml"
    cfg.save(cfg.Config(), path)  # default layout is None
    data = tomllib.loads(path.read_text("utf-8"))
    assert "layout" not in data["shell"]


def test_layout_round_trips_when_set(tmp_path) -> None:
    c = cfg.Config()
    c.shell.layout = "AAAA"  # opaque base64
    path = tmp_path / "config.toml"
    cfg.save(c, path)
    assert cfg.load(path).shell.layout == "AAAA"


def test_from_dict_ignores_unknown_keys() -> None:
    c = cfg.Config.from_dict({"shell": {"window_width": 800, "bogus": 1}, "extra": {}})
    assert c.shell.window_width == 800
    assert c.shell.window_height == 760  # default preserved


def test_tools_parsed_and_incomplete_dropped() -> None:
    c = cfg.Config.from_dict(
        {"tools": [{"name": "a", "command": "x"}, {"name": "missing-command"}]}
    )
    assert c.tools == [cfg.ToolConfig(name="a", command="x")]


def test_env_override_path(monkeypatch, tmp_path) -> None:
    target = tmp_path / "custom.toml"
    monkeypatch.setenv(cfg.ENV_VAR, str(target))
    assert cfg.default_config_path() == target


@pytest.mark.parametrize(
    ("level", "expected_present"),
    [("DEBUG", True), ("info", True), ("NONSENSE", True)],
)
def test_configure_logging_never_raises(level, expected_present) -> None:
    from benchhud.logging_setup import configure_logging

    configure_logging(level)  # must not raise, even on a bogus level
    assert expected_present
