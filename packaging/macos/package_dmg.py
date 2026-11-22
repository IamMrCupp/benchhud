"""Build the notarized macOS .dmg with a stapled app inside it (#426).

``briefcase package -p dmg`` signs the app, builds the disk image, notarizes
the image and staples the ticket to the image. The app inside never gets one:
it was sealed into the image before Apple had seen it. Copy that app into
Applications and its ticket stays behind, so Gatekeeper has to reach Apple on
first launch, and a Mac that's offline at that moment can't.

So this does it in the order that staples both:

1. ``briefcase package -p zip`` signs the app, has Apple notarize it and
   staples the ticket to the app in ``build/``. (Briefcase's zip route staples
   the app, since a zip can't carry a ticket.)
2. The disk image is built here, around that stapled app, with the layout
   Briefcase gives its own. Not by ``briefcase package -p dmg``: that signs the
   app again first, and a new signature is a new app as far as the ticket goes.
3. The image is signed, notarized and stapled.

    python packaging/macos/package_dmg.py "Developer ID Application: NAME (TEAMID)"

Run it from the repo root, after ``briefcase create`` and ``briefcase build``,
with the Python that has Briefcase installed (``uvx --from briefcase python …``
works). It needs the notary credential Briefcase uses, a keychain profile
named ``briefcase-macOS-<team id>``; see docs/PACKAGING.md.

Two submissions to Apple, so it takes about twice as long as the image alone.
"""

from __future__ import annotations

import json
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

APP = Path("build/benchhud/macos/app/benchhud.app")
ICON = Path("packaging/icons/benchhud.icns")
DIST = Path("dist")

#: Runs a command and raises if it fails. Output goes straight to the terminal
#: as it comes (a notarization takes minutes), unless ``capture`` asks for it
#: back, in which case it is returned.
Runner = Callable[..., str]


class PackagingError(RuntimeError):
    """The .dmg wasn't made. The message says which step stopped it."""


def team_id(identity: str) -> str:
    """The team id out of ``Developer ID Application: NAME (TEAMID)``."""
    match = re.search(r"\(([A-Z0-9]+)\)\s*$", identity)
    if not match:
        raise PackagingError(f"no team id at the end of the identity {identity!r}")
    return match.group(1)


def notary_profile(identity: str) -> str:
    """The keychain profile Briefcase stores the notary credential under."""
    return f"briefcase-macOS-{team_id(identity)}"


def app_version(app: Path) -> str:
    with (app / "Contents" / "Info.plist").open("rb") as stream:
        return str(plistlib.load(stream)["CFBundleShortVersionString"])


def dmg_path(app: Path, dist: Path = DIST) -> Path:
    """Where the image goes: the name Briefcase gives it, so releases don't change."""
    return dist / f"{app.stem}-{app_version(app)}.dmg"


def dmg_settings(app: Path, icon: Path | None) -> dict[str, object]:
    """The disk image's layout, as ``briefcase package -p dmg`` lays its own out.

    Kept the same on purpose, so the image looks as it did before this script
    took over building it: the app beside an Applications shortcut.
    """
    settings: dict[str, object] = {
        "files": [str(app)],
        "symlinks": {"Applications": "/Applications"},
        "icon_locations": {app.name: (75, 75), "Applications": (225, 75)},
        "window_rect": ((600, 600), (350, 150)),
        "icon_size": 64,
        "text_size": 12,
    }
    if icon is not None and icon.exists():
        settings["icon"] = str(icon)
    return settings


def notary_status(output: str) -> tuple[str, str]:
    """``(status, submission id)`` from ``notarytool submit --output-format json``."""
    try:
        answer = json.loads(output)
    except json.JSONDecodeError as exc:
        raise PackagingError(f"notarytool didn't answer in JSON: {output.strip()!r}") from exc
    return str(answer.get("status", "")), str(answer.get("id", ""))


def _run(command: Sequence[str], *, capture: bool = False) -> str:
    print("$", " ".join(str(part) for part in command), flush=True)
    done = subprocess.run(
        list(command), stdout=subprocess.PIPE if capture else None, text=True, check=False
    )
    if capture and done.stdout:
        print(done.stdout, end="", flush=True)
    if done.returncode != 0:
        raise PackagingError(f"{command[0]} exited with {done.returncode}")
    return done.stdout or ""


def _build_dmg(dmg: Path, volume_name: str, settings: dict[str, object]) -> None:
    import dmgbuild  # Installed with Briefcase, on macOS only.

    dmgbuild.build_dmg(filename=str(dmg), volume_name=volume_name, settings=settings)


def package(
    identity: str,
    *,
    app: Path = APP,
    icon: Path = ICON,
    dist: Path = DIST,
    run: Runner = _run,
    build_dmg: Callable[[Path, str, dict[str, object]], None] = _build_dmg,
    python: str = sys.executable,
) -> Path:
    """Make the notarized .dmg with a stapled app in it; returns its path."""
    profile = notary_profile(identity)
    if not app.is_dir():
        raise PackagingError(f"{app} isn't built: run briefcase create and build first")

    # 1. The app: signed, notarized, and stapled in build/ by Briefcase.
    briefcase = [python, "-m", "briefcase", "package", "macOS", "app", "-p", "zip"]
    run([*briefcase, "--identity", identity, "--no-input"])
    run(["xcrun", "stapler", "validate", str(app)])
    # The zip was only the vehicle for the notary service; the release is the .dmg.
    for leftover in dist.glob("*.app.zip"):
        leftover.unlink()

    # 2. The image, around the stapled app. Nothing signs the app after this.
    dmg = dmg_path(app, dist)
    dist.mkdir(parents=True, exist_ok=True)
    dmg.unlink(missing_ok=True)
    build_dmg(dmg, f"{app.stem} {app_version(app)}", dmg_settings(app, icon))

    # 3. The image: signed, notarized, stapled.
    run(["codesign", "--sign", identity, "--force", "--timestamp", str(dmg)])
    submit = ["xcrun", "notarytool", "submit", str(dmg), "--keychain-profile", profile]
    answer = run([*submit, "--wait", "--output-format", "json"], capture=True)
    status, submission = notary_status(answer)
    if status != "Accepted":
        if submission:
            run(["xcrun", "notarytool", "log", submission, "--keychain-profile", profile])
        raise PackagingError(f"Apple's notary service answered {status or 'nothing'} for {dmg}")
    run(["xcrun", "stapler", "staple", str(dmg)])
    run(["xcrun", "stapler", "validate", str(dmg)])
    return dmg


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1 or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0 if argv and argv[0] in {"-h", "--help"} else 2
    try:
        dmg = package(argv[0])
    except PackagingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"built {dmg}: notarized and stapled, and so is the app inside it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
