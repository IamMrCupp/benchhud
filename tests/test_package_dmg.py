"""Tests for the macOS .dmg packaging script (#426), without a Mac.

The script signs and notarizes, which only CI's macOS job can do for real. What
is pinned here is the part that broke: the *order*. The app has to be notarized
and stapled before the disk image is built around it, and nothing may sign the
app after that. Every command goes through an injected runner, so the tests run
on Linux and nothing reaches Apple.
"""

from __future__ import annotations

import importlib.util
import json
import plistlib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "macos" / "package_dmg.py"
_spec = importlib.util.spec_from_file_location("package_dmg", SCRIPT)
assert _spec is not None and _spec.loader is not None
package_dmg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(package_dmg)

IDENTITY = "Developer ID Application: EXAMPLE PERSON (ABCDE12345)"


def _app(tmp_path: Path, version: str = "1.2.3") -> Path:
    app = tmp_path / "build" / "benchhud.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleShortVersionString": version}, stream)
    return app


class _Recorder:
    """Stands in for the shell and for dmgbuild: one list of what happened, in order."""

    def __init__(self, notary: dict[str, str] | None = None) -> None:
        self.steps: list[list[str]] = []
        self.notary = notary if notary is not None else {"status": "Accepted", "id": "sub-1"}
        self.built: list[tuple[Path, str, dict]] = []

    def run(self, command, *, capture: bool = False) -> str:
        self.steps.append([str(part) for part in command])
        if command[:3] == ["xcrun", "notarytool", "submit"]:
            assert capture, "the notary answer has to be read, not just shown"
            return json.dumps(self.notary)
        return ""

    def build_dmg(self, dmg: Path, volume_name: str, settings: dict) -> None:
        self.steps.append(["dmgbuild", str(dmg)])
        self.built.append((dmg, volume_name, settings))
        dmg.write_bytes(b"dmg")

    def index(self, *start: str) -> int:
        """Where the first step beginning with ``start`` is."""
        for position, step in enumerate(self.steps):
            if step[: len(start)] == list(start):
                return position
        raise AssertionError(f"no step starts with {start}: {self.steps}")


def _package(tmp_path: Path, recorder: _Recorder, app: Path | None = None) -> Path:
    return package_dmg.package(
        IDENTITY,
        app=app or _app(tmp_path),
        icon=tmp_path / "no-icon.icns",
        dist=tmp_path / "dist",
        run=recorder.run,
        build_dmg=recorder.build_dmg,
        python="python",
    )


# --- the order, which is the fix ----------------------------------------------


def test_the_app_is_notarized_and_stapled_before_the_image_is_built(tmp_path) -> None:
    """A ticket can't be added to an app that's already sealed in the image."""
    recorder = _Recorder()
    _package(tmp_path, recorder)
    briefcase = recorder.index("python", "-m", "briefcase", "package")
    stapled = recorder.index("xcrun", "stapler", "validate")
    image = recorder.index("dmgbuild")
    assert briefcase < stapled < image
    assert "benchhud.app" in recorder.steps[stapled][-1]


def test_briefcase_takes_the_zip_route_which_staples_the_app(tmp_path) -> None:
    """Its dmg route staples only the image, which is the bug."""
    recorder = _Recorder()
    _package(tmp_path, recorder)
    command = recorder.steps[recorder.index("python", "-m", "briefcase")]
    assert command[command.index("-p") + 1] == "zip"
    assert command[command.index("--identity") + 1] == IDENTITY
    assert "--no-notarize" not in command
    assert "--adhoc-sign" not in command


def test_nothing_signs_the_app_once_it_is_stapled(tmp_path) -> None:
    """A new signature is a new app to the ticket: briefcase runs once, first."""
    recorder = _Recorder()
    app = _app(tmp_path)
    _package(tmp_path, recorder, app)
    after = recorder.steps[recorder.index("xcrun", "stapler", "validate") + 1 :]
    assert not any(step[:3] == ["python", "-m", "briefcase"] for step in after)
    signed = [step[-1] for step in after if step[0] == "codesign"]
    assert signed and all(target.endswith(".dmg") for target in signed)


def test_the_image_is_signed_notarized_then_stapled(tmp_path) -> None:
    recorder = _Recorder()
    dmg = _package(tmp_path, recorder)
    order = [
        recorder.index("dmgbuild"),
        recorder.index("codesign"),
        recorder.index("xcrun", "notarytool", "submit"),
        recorder.index("xcrun", "stapler", "staple"),
    ]
    assert order == sorted(order)
    assert recorder.steps[order[3]][-1] == str(dmg)
    assert "--timestamp" in recorder.steps[order[1]]  # the notary service requires one
    # And checked: the last thing it does is validate the image's own ticket.
    assert recorder.steps[-1] == ["xcrun", "stapler", "validate", str(dmg)]


def test_the_notary_profile_is_the_one_briefcase_stores(tmp_path) -> None:
    recorder = _Recorder()
    _package(tmp_path, recorder)
    submit = recorder.steps[recorder.index("xcrun", "notarytool", "submit")]
    assert submit[submit.index("--keychain-profile") + 1] == "briefcase-macOS-ABCDE12345"
    assert "--wait" in submit


# --- what comes out -----------------------------------------------------------


def test_the_image_keeps_the_name_briefcase_gave_it(tmp_path) -> None:
    """Releases, the Homebrew cask and the docs all name ``benchhud-<version>.dmg``."""
    recorder = _Recorder()
    dmg = _package(tmp_path, recorder, _app(tmp_path, "0.23.1"))
    assert dmg == tmp_path / "dist" / "benchhud-0.23.1.dmg"
    assert recorder.built[0][1] == "benchhud 0.23.1"


def test_the_image_is_laid_out_as_briefcase_lays_its_own(tmp_path) -> None:
    app = _app(tmp_path)
    settings = package_dmg.dmg_settings(app, None)
    assert settings["files"] == [str(app)]
    assert settings["symlinks"] == {"Applications": "/Applications"}
    assert settings["icon_locations"] == {"benchhud.app": (75, 75), "Applications": (225, 75)}
    assert "icon" not in settings


def test_the_icon_is_used_when_it_exists(tmp_path) -> None:
    icon = tmp_path / "benchhud.icns"
    icon.write_bytes(b"icns")
    app = _app(tmp_path)
    assert package_dmg.dmg_settings(app, icon)["icon"] == str(icon)
    assert "icon" not in package_dmg.dmg_settings(app, tmp_path / "missing.icns")


def test_the_zip_briefcase_leaves_behind_is_removed(tmp_path) -> None:
    """It was the vehicle for the notary service; a release attaches the .dmg."""
    dist = tmp_path / "dist"
    dist.mkdir()
    recorder = _Recorder()
    real_run = recorder.run

    def run(command, *, capture: bool = False) -> str:
        if command[:3] == ["python", "-m", "briefcase"]:
            (dist / "benchhud-1.2.3.app.zip").write_bytes(b"zip")
        return real_run(command, capture=capture)

    recorder.run = run  # type: ignore[method-assign]
    _package(tmp_path, recorder)
    assert [p.name for p in dist.iterdir()] == ["benchhud-1.2.3.dmg"]


def test_an_old_image_is_replaced_not_appended_to(tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "benchhud-1.2.3.dmg").write_bytes(b"stale image from the last run")
    dmg = _package(tmp_path, _Recorder())
    assert dmg.read_bytes() == b"dmg"


# --- when it goes wrong -------------------------------------------------------


def test_a_rejected_image_fails_and_fetches_apples_log(tmp_path) -> None:
    """notarytool exits 0 on a rejection; the status is the only place it says so."""
    recorder = _Recorder(notary={"status": "Invalid", "id": "sub-9"})
    with pytest.raises(package_dmg.PackagingError, match="Invalid"):
        _package(tmp_path, recorder)
    log = recorder.steps[recorder.index("xcrun", "notarytool", "log")]
    assert log[3] == "sub-9"
    assert not any(step[:3] == ["xcrun", "stapler", "staple"] for step in recorder.steps)


def test_an_app_that_is_not_built_stops_before_anything_runs(tmp_path) -> None:
    recorder = _Recorder()
    with pytest.raises(package_dmg.PackagingError, match="isn't built"):
        _package(tmp_path, recorder, tmp_path / "build" / "missing.app")
    assert recorder.steps == []


def test_an_identity_without_a_team_id_is_refused(tmp_path) -> None:
    with pytest.raises(package_dmg.PackagingError, match="team id"):
        package_dmg.notary_profile("Developer ID Application: NOBODY")


def test_a_notary_answer_that_is_not_json_is_an_error() -> None:
    with pytest.raises(package_dmg.PackagingError, match="JSON"):
        package_dmg.notary_status("Conducting pre-submission checks…")


def test_usage_without_an_identity(capsys) -> None:
    assert package_dmg.main([]) == 2
    assert "package_dmg.py" in capsys.readouterr().out
