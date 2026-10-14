# benchhud

A heads-up display for a microsoldering repair bench. It fuses a live microscope
feed with a thermal overlay — so the component that's cooking lights up on the
scope image, labeled with its temperature — reads your bench instruments over
serial, and pushes the whole composite out as a virtual camera for OBS.

It started as "I want to see which part is overheating without looking away from
the scope" and turned into the bench's command center.

> **Status: early.** Installable from each release (see *Install*), and verified
> on real hardware for scope capture and macOS virtual-camera output into OBS.
> The rest of the device-facing work (raw Y16 thermal, the OWON's write
> commands, Windows virtual-camera output) is built and tested off-hardware and
> waits on the bench. Not a daily driver yet.

## What it does

- **Scope + thermal fusion** — a thermal camera registered onto the microscope
  image, so the heat bloom sits over the actual component. Heatmap blend,
  threshold tint, or a marker with a live temperature label. This is the headline.
- **Instrument telemetry** — live DMM / PSU readings over SCPI in a bench-style
  meter with peak-hold and a V/A/W trend graph, on-screen and optionally burned
  into the stream.
- **Repair jobs** — a local-first log of what's on the bench. One button attaches
  the current scope frame, thermal frame, and meter readings to the job timeline,
  and an over-current trip logs itself. Export a customer-facing report when it's
  done. [More →](docs/JOBS.md)
- **Ticket import** — optionally pull the open queue from a shop intake system
  and drop a ticket onto the bench as a job. Runs standalone without one.
  [More →](docs/INTAKE.md)
- **Board docs** — a local library of board-view files keyed by part number.
  "Do I have this board?" answered from the job, from a quick search, or across
  the whole open ticket queue as a boards-to-source list. Opens in OpenBoardView
  or FlexBV; file a schematic PDF beside the board and FlexBV cross-probes it.
  [More →](docs/BOARDS.md)
- **Tool launcher** — opens the repair tools you already use (OpenBoardView,
  minipro-gui, whatever's in your config) straight from the HUD.
- **Virtual camera out** — the composite is written to a v4l2loopback device, so
  OBS treats it as a normal camera source.
- **OBS browser sources** — the meter, the power graph and the job card, each
  served as its own transparent page on localhost, so every panel is a separate
  OBS source you can place anywhere. Never shows the customer.
  [More →](docs/BROWSER-SOURCES.md)
- **Terminal** — your shell in a panel, and optionally on stream as a read-only
  OBS source with a pause button for anything private. macOS and Linux.
  [More →](docs/TERMINAL.md)
- **Flash** — read, back up and write SPI flash chips with flashrom. A backup is
  two reads that must match, and every dump is filed with the job.
  [More →](docs/FLASH.md)

Everything lives in dockable panels — arrange the bench the way you work, and the
layout sticks across restarts.

## How it works

One process owns the cameras and builds the composite; OBS gets the output, never
the raw devices. That's deliberate — **a UVC camera allows one reader at a time**,
so benchhud is the sole reader of both the scope and thermal cams, and OBS pulls
the composited virtual cam. If OBS "can't open" a camera, that's why.

The pipeline is the obvious one — capture → register → analyze → composite →
output — with each camera on its own thread feeding a latest-frame buffer, so a
slow feed never stalls the loop. The thermal side reads the raw **Y16** stream
(not the pre-colormapped MJPEG) and converts to real per-pixel °C. That's what
makes "that pad is 78°C" possible instead of just a pretty picture.

## Platforms

Linux-first, because that's the OS the reference bench runs — not because the
others are optional. **macOS and Windows are committed targets**, not ports
someone might get to. The app itself is portable and runs on all three today;
what's platform-specific is virtual-camera output, which talks to a different OS
interface on each: v4l2loopback on Linux ([BENCH-SETUP.md](docs/BENCH-SETUP.md)),
OBS's camera extension on macOS ([MACOS-SETUP.md](docs/MACOS-SETUP.md)), and a
Windows backend still to come. Where a backend isn't available, the status bar
says so rather than streaming nothing quietly.

## Install

Every [release](https://github.com/IamMrCupp/benchhud/releases) carries its
installers, built and install-checked by CI at the release tag.

**Ubuntu (the bench).** The `.deb` from the latest release, for Ubuntu 26.04
on amd64:

```sh
gh release download --repo IamMrCupp/benchhud --pattern '*.deb'
sudo apt install ./benchhud_*.deb
```

`apt install` (not `dpkg -i`) pulls in the libraries Qt needs. It shows up in
the app menu as **benchhud**. Two one-time steps are left to you, since a
package shouldn't do them behind your back: join the `video`, `dialout` and
`plugdev` groups, and load v4l2loopback for virtual-camera output. Both are in
[BENCH-SETUP.md](docs/BENCH-SETUP.md).

**Windows.** The `.msi` from the latest release. It installs per user, with no
admin rights. It's ad-hoc signed, so SmartScreen warns on first run. The HUD
runs, but two pieces aren't there yet: virtual-camera output (#124) and the
terminal panel (#203).

**macOS.** No download: macOS grants camera access per app *signature*, so the
app has to be built and signed with a Developer ID, which
[PACKAGING.md](docs/PACKAGING.md) walks through. For OBS, see
[MACOS-SETUP.md](docs/MACOS-SETUP.md).

**From source (any OS).** `uv run benchhud`; see *Development* below.

The shop side runs separately, with its own installers (Linux service `.deb`,
Windows and macOS tray apps, Docker, Kubernetes):
[benchhud-intake](https://github.com/IamMrCupp/benchhud-intake) for tickets and
[bench-parts](https://github.com/IamMrCupp/bench-parts) for parts inventory.

## Hardware

benchhud isn't locked to specific gear — device paths are config — but it's
developed against:

- **Scope:** a UVC microscope camera (BTER 4K).
- **Thermal:** Sipeed T256s — 256×192 LWIR, raw Y16 for true temperatures.
- **Instrument:** OWON SPM8104 (PSU + DMM) over SCPI serial.

Set your device paths in `config.example.toml`, or pick them from dropdowns in
**Preferences** once the app is running.

## Development

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync            # env + deps from the lockfile
uv run benchhud    # launch
uv run pytest      # tests
uv run ruff check  # lint
```

The whole thing runs without hardware — the default source is a synthetic test
pattern, so `uv run benchhud` opens a working shell you can poke at.

| Doc | What's in it |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Module map, the hardware seams, design rules. |
| [BENCH-SETUP.md](docs/BENCH-SETUP.md) | Bringing it up on the Linux bench — cameras, the OWON, v4l2loopback → OBS. |
| [MACOS-SETUP.md](docs/MACOS-SETUP.md) | Publishing into OBS's virtual camera on a Mac. |
| [PACKAGING.md](docs/PACKAGING.md) | The packages: the Ubuntu `.deb`, the Windows MSI, the signed macOS `.app`, releases, and what CI builds when. |
| [BROWSER-SOURCES.md](docs/BROWSER-SOURCES.md) | The data panels as OBS Browser sources: URLs, what goes on air. |
| [TERMINAL.md](docs/TERMINAL.md) | The terminal panel and its read-only stream view. |
| [FLASH.md](docs/FLASH.md) | The Flash panel: backing up and writing SPI chips with flashrom. |
| [JOBS.md](docs/JOBS.md) | The repair log: jobs, captures, fault logging, reports. |
| [INTAKE.md](docs/INTAKE.md) | Pulling tickets from a shop intake system. |
| [BOARDS.md](docs/BOARDS.md) | The board-view library: find, open, and source boards. |

## License

Source-available under the [PolyForm Small Business License 1.0.0](https://polyformproject.org/licenses/small-business/1.0.0/)
— see [LICENSE](LICENSE). Free to use, modify and distribute if your company has
fewer than 100 people and under $1M in annual revenue, which covers personal use
and most small shops. Above that, a commercial licence is required — get in touch.

Note this is *source-available*, not open source: the code is readable and
freely usable within those limits, but it isn't an OSI-approved licence.

The Y16→°C conversion follows Sipeed's documented spec for the T256s; credit to
them for the formula.

The meter's segmented-LCD digits use the [DSEG](https://github.com/keshikan/DSEG)
font by keshikan, bundled under the SIL Open Font License 1.1 — see
[resources/fonts/OFL.txt](src/benchhud/resources/fonts/OFL.txt).
