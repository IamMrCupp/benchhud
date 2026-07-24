# benchhud

A heads-up display for a microsoldering repair bench. It fuses a live microscope
feed with a thermal overlay — so the component that's cooking lights up on the
scope image, labeled with its temperature — reads your bench instruments over
serial, and pushes the whole composite out as a virtual camera for OBS.

It started as "I want to see which part is overheating without looking away from
the scope" and turned into the bench's command center.

> **Status: early.** The application is built and tested off-hardware — shell,
> capture pipeline, thermal math, overlay compositing, instrument driver, live
> controls, repair log. Scope capture is verified against a real camera; the rest
> of the device-facing work (thermal Y16, virtual cam, the OWON on serial) sits
> behind tested interfaces and gets wired up on the bench. Not a daily driver yet.

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
- **Tool launcher** — opens the repair tools you already use (OpenBoardView,
  minipro-gui, whatever's in your config) straight from the HUD.
- **Virtual camera out** — the composite is written to a v4l2loopback device, so
  OBS treats it as a normal camera source.

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

Linux-first — that's where the bench lives and where v4l2loopback does the
virtual-cam work. The rest of the app is portable and runs on macOS today, but
virtual-camera output is Linux-only: it writes to a loopback device directly.
macOS and Windows output will need their own backend.

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
| [JOBS.md](docs/JOBS.md) | The repair log: jobs, captures, fault logging, reports. |
| [INTAKE.md](docs/INTAKE.md) | Pulling tickets from a shop intake system. |

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
