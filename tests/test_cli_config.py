"""``benchhud config``: scripted config changes for bench-setup (#334)."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys

import pytest

from benchhud import config as config_module
from benchhud.app import main as app_main
from benchhud.cli_config import main


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "config.toml"
    monkeypatch.setenv(config_module.ENV_VAR, str(path))
    return path


def test_set_writes_a_string_field(cfg_path) -> None:
    assert main(["set", "intake.base_url", "http://127.0.0.1:8401"]) == 0
    assert config_module.load(cfg_path).intake.base_url == "http://127.0.0.1:8401"


def test_set_reads_the_value_from_stdin_without_the_trailing_newline(cfg_path) -> None:
    assert main(["set", "inventory.token", "-"], stdin=io.StringIO("s3cret\n")) == 0
    assert config_module.load(cfg_path).inventory.token == "s3cret"


def test_set_never_echoes_the_value(cfg_path, capsys) -> None:
    main(["set", "recon.token", "-"], stdin=io.StringIO("do-not-print"))
    out = capsys.readouterr()
    assert "do-not-print" not in out.out + out.err


def test_set_keeps_other_values(cfg_path) -> None:
    main(["set", "intake.base_url", "http://a"])
    main(["set", "intake.token", "t"])
    loaded = config_module.load(cfg_path)
    assert (loaded.intake.base_url, loaded.intake.token) == ("http://a", "t")


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("instruments.control", "true", True),
        ("instruments.control", "off", False),
        ("output.fps", "30", 30),
        ("instruments.max_voltage", "3.3", 3.3),
    ],
)
def test_set_parses_the_fields_type(cfg_path, key, raw, expected) -> None:
    assert main(["set", key, raw]) == 0
    section, name = key.split(".")
    assert getattr(getattr(config_module.load(cfg_path), section), name) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("intake.nope", "x"),  # unknown field
        ("nope.base_url", "x"),  # unknown section
        ("intake", "x"),  # no field
        ("panels.scope", "x"),  # a section, not a value
        ("tools.name", "x"),  # a list
        ("output.fps", "fast"),  # wrong type
        ("instruments.control", "maybe"),
    ],
)
def test_set_refuses_bad_input_and_writes_nothing(cfg_path, key, raw, capsys) -> None:
    assert main(["set", key, raw]) == 2
    assert not cfg_path.exists()
    assert "benchhud config:" in capsys.readouterr().err


def test_path_prints_the_config_location(cfg_path, capsys) -> None:
    assert main(["path"]) == 0
    assert capsys.readouterr().out.strip() == str(cfg_path)


def test_the_app_entry_point_routes_config(cfg_path) -> None:
    assert app_main(["config", "set", "intake.token", "abc"]) == 0
    assert config_module.load(cfg_path).intake.token == "abc"


def test_config_runs_without_importing_qt(tmp_path) -> None:
    """bench-setup runs it over SSH and in CI, with no display."""
    code = (
        "import sys; from benchhud.app import main; "
        "rc = main(['config', 'set', 'intake.base_url', 'http://x']); "
        "assert rc == 0, rc; "
        "assert not any(m.startswith('PySide6') for m in sys.modules), 'Qt imported'"
    )
    env = {**os.environ, config_module.ENV_VAR: str(tmp_path / "c.toml")}
    subprocess.run([sys.executable, "-c", code], env=env, check=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_saved_config_is_owner_only(tmp_path) -> None:
    path = tmp_path / "config.toml"
    config_module.save(config_module.Config(), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_saving_tightens_an_existing_world_readable_config(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("", "utf-8")
    path.chmod(0o644)
    config_module.save(config_module.Config(), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
