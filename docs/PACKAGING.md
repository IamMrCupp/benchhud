# Packaging

benchhud ships as a native app built with [Briefcase](https://briefcase.readthedocs.io/).
So far that means a signed **macOS** `.app` and an **Ubuntu** `.deb`. Windows comes later.

All config lives in `pyproject.toml` under `[tool.briefcase]`. Name, version,
licence and dependencies come from `[project]`, since Briefcase reads PEP 621.
The Briefcase section only holds what a bundle adds on top of a wheel: bundle
ID, icon, camera permission, the macOS floor and the Ubuntu runtime libraries.

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
startup. The scope shows the test pattern while the prompt is up, and the status
bar says why. After you click **Allow**, the live feed takes over within a few
seconds, with no relaunch (#175).

---

## Ubuntu (the bench)

A native `.deb`: it installs to `/usr`, adds **benchhud** to the app menu with its
icon, and pulls in the system libraries Qt needs through `apt`.

### Install

Copy the `.deb` to the bench, then:

```sh
sudo apt install ./benchhud_<version>-1~ubuntu-<codename>_amd64.deb
```

Using `apt install ./file.deb` (not `dpkg -i`) means the dependencies install too.
Launch it from the app menu, or run `benchhud` in a terminal.

**One-time bench setup** (same as [BENCH-SETUP.md](BENCH-SETUP.md), not
something a package should do behind your back):

- Put yourself in the device groups, then log out and back in:
  `sudo usermod -aG dialout,video,plugdev "$USER"`
- Virtual-cam output needs **v4l2loopback**. It's a kernel module, so it's a
  documented step rather than a package dependency. See BENCH-SETUP.md →
  v4l2loopback.

Unlike macOS, Linux has no per-app camera permission. The groups are the whole
story.

### Build (from the Mac, via Docker)

Briefcase builds a `.deb` inside a container matching the target Ubuntu release,
so it links against that release's own Python and libraries. The bench is x86_64,
so on an Apple Silicon Mac, tell Docker to build for amd64. It runs emulated and
takes a while the first time.

```sh
scripts/build_deb.sh          # Ubuntu 26.04 by default; pass another tag if needed
```

The `.deb` lands in `dist/`. The script runs Briefcase's `create`, `build` and
`package` for `linux system` against the Ubuntu image. **On an Apple Silicon Mac
the last step fails under emulation**: `tar: Cannot stat: Function not
implemented`, because QEMU lacks a stat syscall that newer `tar` uses. So the
script packs the staged files with `dpkg-deb` in a native arm64 container
instead. `dpkg-deb` only archives files, so the result is still a correct amd64
package. Turning on Docker Desktop's *Use Rosetta for x86_64/amd64 emulation*
may let Briefcase's own step work.

The first build is slow: pulling the image and installing PySide6 and OpenCV,
all emulated, can take 10+ minutes. On the bench itself, build natively with
`uvx briefcase package linux system` (no Docker, no `--target`).

**Why not Flatpak or AppImage:** benchhud opens raw `/dev/video*`,
`/dev/ttyUSB*` and a v4l2loopback node. Flatpak's sandbox fights all three, and a
system package just uses your groups.

---

## Notes

- **macOS 13+.** PySide6 6.8+ and opencv-python 5 only publish `macosx_13_0` wheels.
- **arm64 only for now** (`universal_build = false`). opencv-python has no
  universal2 wheel, so an Intel build would need its own run on an Intel Mac.
- **Briefcase doesn't read `uv.lock`.** It resolves `[project].dependencies` with
  pip, so a bundle can pick up newer versions than the dev env. Pin in
  `[tool.briefcase.app.benchhud]` `requires` if that ever bites.
- **The icons** are `packaging/icons/benchhud.icns` (macOS) and
  `benchhud-<size>.png` (Linux), generated from `src/benchhud/resources/icon.png`
  by `scripts/make_icns.sh`. Rerun that script if the PNG changes.
- **Not done yet:** notarization and a `.dmg` for handing to other people (#173),
  CI builds, and a Windows bundle.
