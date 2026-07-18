# benchhud

A microsoldering / board-repair bench HUD. It fuses a live microscope feed with
a thermal camera overlay, shows live instrument telemetry, and outputs a
composited **virtual camera** for OBS — real-time board-repair diagnosis, on
stream.

> **Status:** early development — Phase 0 (foundation & HUD shell). Not yet usable.

## What it does (the plan)

benchhud is a HUD **shell** that hosts pluggable **panels**:

- **Scope** — live microscope (visible) video.
- **Thermal fusion** — a thermal camera registered onto the scope image so a
  hot component's thermal bloom sits over that component, labeled with its live
  temperature. The headline feature.
- **Instrument telemetry** — live DMM / PSU readings over SCPI serial.
- **Tool launcher** — opens the adjacent repair tools you already use
  (OpenBoardView, minipro-gui, …).

The composited output is pushed to a virtual camera (`pyvirtualcam` —
v4l2loopback on Linux, OBS virtual cam on macOS/Windows) so OBS picks it up as a
normal source.

**Platforms:** Linux-first (the reference bench is Ubuntu); macOS and Windows are
later portability targets.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync            # create the venv and install deps from uv.lock
uv run benchhud    # launch (placeholder until the shell lands)
uv run pytest      # run tests
uv run ruff check  # lint
```

Copy [`config.example.toml`](config.example.toml) to configure device paths and
panels.

## License

MIT — see [LICENSE](LICENSE).
