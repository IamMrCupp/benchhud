# Packaging

benchhud ships as a native app built with [Briefcase](https://briefcase.readthedocs.io/).
So far that means **macOS**; Linux and Windows bundles come later.

All config lives in `pyproject.toml` under `[tool.briefcase]`. Name, version,
licence and dependencies come from `[project]`, since Briefcase reads PEP 621.
The Briefcase section only holds what a bundle adds on top of a wheel: bundle
ID, icon, camera permission and the macOS floor.

---

## Why a bundle, not `uv run`

On macOS, camera access is granted **per app**. When you launch benchhud from a
terminal, it isn't the app asking. The terminal is, and depending on how it was
launched, it may never show a prompt at all. OpenCV then logs `not authorized to
capture video`, and the scope drops to the synthetic feed.

A bundle carries its own identity (`com.iammrcupp.benchhud`) and its own
`NSCameraUsageDescription`. So macOS asks once, *"benchhud would like to access
the camera"*, and remembers the answer under **Privacy & Security → Camera**.

`uv run benchhud` is still the dev loop. Use the bundle to test anything that
touches a camera on a Mac.

---

## Build (macOS)

Needs Xcode command-line tools and [uv](https://docs.astral.sh/uv/).

```sh
uvx briefcase create macOS app    # first time, or after dependency changes
uvx briefcase build macOS app     # ad-hoc signed .app in build/
uvx briefcase run macOS app       # build + launch
```

To sign with the Developer ID (you need this for the camera grant to stick, see below):

```sh
uvx briefcase package macOS app -p zip --no-notarize \
  --identity "Developer ID Application: AARON MICHAEL CUPP (C4MTP3Z42F)"
```

This signs `build/benchhud/macos/app/benchhud.app` in place, with hardened runtime
and the camera entitlement, and writes `dist/benchhud-<version>.app.zip`.

### Sign it, even locally

macOS ties a camera grant to the app's **code signature**. An ad-hoc signature
changes on every build, so every `briefcase build` looks like a brand-new app,
and it asks again. Once the app is signed with the Developer ID, the grant
survives rebuilds.

### First launch

The camera prompt appears the first time benchhud opens a camera, which is at
startup. The scope falls back to the synthetic feed while the prompt is up.
**Relaunch once after clicking Allow.** Retrying after a grant, and saying in the
status bar why the scope is synthetic, is tracked in #171.

---

## Notes

- **macOS 13+.** PySide6 6.8+ and opencv-python 5 only publish `macosx_13_0` wheels.
- **arm64 only for now** (`universal_build = false`). opencv-python has no
  universal2 wheel, so an Intel build would need its own run on an Intel Mac.
- **Briefcase doesn't read `uv.lock`.** It resolves `[project].dependencies` with
  pip, so a bundle can pick up newer versions than the dev env. Pin in
  `[tool.briefcase.app.benchhud]` `requires` if that ever bites.
- **The icon** is `packaging/icons/benchhud.icns`, generated from
  `src/benchhud/resources/icon.png` by `scripts/make_icns.sh`. Rerun that script
  if the PNG changes.
- **Not done yet:** notarization and a `.dmg` for handing to other people, CI
  builds, and Linux/Windows bundles.
