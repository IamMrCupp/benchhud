# benchhud

A heads-up display for a microsoldering repair bench. It fuses a live microscope
feed with a thermal overlay — so the component that's cooking lights up on the
scope image, labeled with its temperature — reads your bench instruments over
serial, and pushes the whole composite out as a virtual camera for OBS.

It started as "I want to see which part is overheating without looking away from
the scope" and turned into the bench's command center.

> **Status: early.** The full application is built and tested off-hardware —
> shell, capture pipeline, thermal math, overlay compositing, instrument driver,
> live controls. The device-facing pieces (cameras, virtual cam, serial) sit
> behind tested interfaces and get wired up on the bench. Not a daily driver yet.

## What it does

- **Scope + thermal fusion** — a thermal camera registered onto the microscope
  image, so the heat bloom sits over the actual component. Heatmap blend,
  threshold tint, or a marker with a live temperature label. This is the headline.
- **Instrument telemetry** — live DMM / PSU readings over SCPI, on-screen and
  burned into the stream.
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

Set your device paths in `config.example.toml`.

## Development

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync            # env + deps from the lockfile
uv run benchhud    # launch
uv run pytest      # tests
uv run ruff check  # lint
```

The whole thing runs without hardware — the default source is a synthetic test
pattern, so `uv run benchhud` opens a working shell you can poke at. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the module map, and
[docs/BENCH-SETUP.md](docs/BENCH-SETUP.md) for bringing it up on the Linux bench
(cameras, the OWON, and the v4l2loopback → OBS output).

## License

MIT — see [LICENSE](LICENSE). The Y16→°C conversion follows Sipeed's documented
spec for the T256s; credit to them for the formula.

The meter's segmented-LCD digits use the [DSEG](https://github.com/keshikan/DSEG)
font by keshikan, bundled under the SIL Open Font License 1.1 — see
[resources/fonts/OFL.txt](src/benchhud/resources/fonts/OFL.txt).
