"""The headless pipeline benchmark still runs and still reports what it says (#390).

``scripts/bench_hud.py`` wraps private pieces of the main window to time them.
This runs it for a second so a rename there fails here, not on the bench.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bench_hud.py"


@pytest.fixture(scope="module")
def bench_hud():
    spec = importlib.util.spec_from_file_location("bench_hud", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_hud"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("bench_hud", None)


def _run(bench_hud, *extra: str) -> dict:
    # A small clip and output keep it quick; the code paths are the same.
    argv = ["--duration", "1.2", "--warmup", "0.6", "--scope-size", "1920x1080"]
    argv += ["--clip-frames", "3", "--size", "320x180", "--fps", "30", *extra]
    return bench_hud.run(bench_hud.build_parser().parse_args(argv))


def test_a_short_run_reports_every_field(bench_hud, qapp) -> None:
    result = _run(bench_hud, "--overlays")

    assert result["schema"] == bench_hud.SCHEMA
    assert result["config"]["reduced_live"] is True
    assert result["clip"]["size"] == [1920, 1080]
    fps = result["fps"]
    assert fps["composite"] > 2
    assert fps["scope_feed"] > 2 and fps["thermal_feed"] > 2
    assert fps["scope_decoded"] > 2 and fps["thermal_in"] > 2
    assert result["cpu"]["total_pct"] > 0
    assert result["memory"]["peak_rss_mb"] > 10

    stages = {row["name"]: row for row in result["stages"]}
    # Every row the table promises, so a stage that stops being timed is noticed.
    assert set(stages) == {name for name, _thread, _depth in bench_hud._STAGE_ROWS}
    assert all(row["calls"] > 0 and row["ms_per_call"] >= 0 for row in stages.values())
    assert stages["scope decode"]["thread"] == "scope reader"

    if result["cpu"]["per_thread_source"] != "none":
        threads = {row["name"] for row in result["cpu"]["threads"]}
        assert {"scope reader", "thermal reader"} <= threads
        assert any(name.startswith("main") for name in threads)

    json.dumps(result)  # the whole thing is plain JSON
    text = bench_hud.format_result(result)
    assert "composite out" in text and "scope decode" in text


def test_the_switches_reach_the_pipeline(bench_hud, qapp) -> None:
    result = _run(bench_hud, "--no-reduced-live", "--no-feeds", "--no-thermal")

    assert result["config"]["reduced_live"] is False
    assert result["fps"]["scope_feed"] is None and result["fps"]["thermal_feed"] is None
    assert result["fps"]["thermal_in"] is None
    names = {row["name"] for row in result["stages"]}
    assert "scope decode" in names and "output (fit + send)" in names
    assert not names & {"scope feed", "thermal feed", "compose (blend)", "thermal tick"}


def test_the_generated_clip_is_the_same_every_run(bench_hud) -> None:
    first = bench_hud.generate_clip(320, 180, 2)
    again = bench_hud.generate_clip(320, 180, 2)
    assert [f.tobytes() for f in first] == [f.tobytes() for f in again]
    assert first[0][:2].tobytes() == b"\xff\xd8"
    assert first[0].tobytes() != first[1].tobytes()  # it moves


def test_compare_lines_two_results_up(bench_hud, tmp_path, capsys) -> None:
    def result(fps: float, decode_ms: float) -> dict:
        return {
            "host": {},
            "clip": {},
            "config": {"reduced_live": decode_ms < 10, "duration_s": 20},
            "fps": {"composite": fps, "scope_feed": None, "thermal_feed": None},
            "cpu": {"total_pct": 40.0, "threads": [{"name": "scope reader", "cpu_pct": 12.0}]},
            "memory": {"peak_rss_mb": 300.0, "rss_mb": None},
            "stages": [{"name": "scope decode", "ms_per_call": decode_ms}],
        }

    before, after = tmp_path / "a.json", tmp_path / "b.json"
    before.write_text(json.dumps(result(15.0, 24.0)))
    after.write_text(json.dumps(result(15.5, 8.0)))
    assert bench_hud.main(["--compare", str(before), str(after)]) == 0
    out = capsys.readouterr().out
    assert "reduced_live False -> True" in out
    assert "-16.00 ms" in out and "+0.50 fps" in out
