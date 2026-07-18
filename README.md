# benchhud

A microsoldering / board-repair bench HUD. It fuses a live microscope feed with
a thermal camera overlay, shows live instrument telemetry, and outputs a
composited virtual camera for OBS — real-time board-repair diagnosis, on stream.

> **Status:** early development (Phase 0 — foundation & HUD shell). Not yet usable.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync            # create the venv and install deps from uv.lock
uv run benchhud    # launch (placeholder until the shell lands)
uv run pytest      # run tests
uv run ruff check  # lint
```

## License

MIT — see [LICENSE](LICENSE).
