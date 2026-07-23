# Architecture

benchhud is a HUD shell that hosts panels, wired to a capture → composite →
output pipeline. Everything device-facing sits behind a small protocol, so the
whole thing runs and is tested without hardware — the real devices drop in on the
bench without touching any consumer.

## Module map

- **`app.py`** — entry point: loads config, builds the `QApplication`, opens the
  shell, starts the preview loop. `--version` stays Qt-free (works headless).
- **`config.py`** — typed config tree + TOML load/save. Deliberately cv2-free.
- **`shell/`** — `MainWindow` (dockable panel host + preview loop), `PanelManager`,
  the `Panel` base, `qt_image` (BGR→QImage), and the panels: `scope`, `thermal`,
  `telemetry`, `controls`, `launcher`.
- **`capture/`** — `FrameSource` protocol, `SyntheticFrameSource`, threaded
  `LatestFrameReader` (latest-frame buffer), `FramePump` (Qt-free dispatch).
- **`output/`** — `OutputSink` protocol, `FakeSink`, `NullSink`, and
  `V4l2LoopbackSink` (`v4l2.py`: writes RGB24 straight to a v4l2loopback node
  via `VIDIOC_S_FMT` + `os.write`, stdlib only).
- **`instruments/`** — `SerialPort` / `LineInstrument` seam, `OwonSpm8104` driver,
  `read_snapshot` (resilient telemetry).
- **`thermal/`** — `convert` (Y16→°C, Sipeed spec), `analyze` (hotspot / threshold
  / stats).
- **`overlay/`** — `RegistrationTransform` (affine warp), `composite`
  (colorize / blend / tint / marker / `compose`), `config_adapter`.
- **`launcher/`** — `ToolLauncher` (subprocess).

## The seams

Every hardware boundary is a protocol with a tested fake. The bench work is
implementing the real side of each — no consumer changes.

| Seam | Protocol | Tested with | Bench implementation |
|---|---|---|---|
| Camera in | `capture.FrameSource` | `SyntheticFrameSource` | `CameraFrameSource` over `cv2.VideoCapture(/dev/videoN)` |
| Virtual cam out | `output.OutputSink` | `FakeSink`, fake `V4l2Device` | `V4l2LoopbackSink` on a real `/dev/videoN` loopback node |
| Instrument | `instruments.SerialPort` | `FakeSerialPort` | `OwonSpm8104.open_device('/dev/ttyUSB0')` |
| Thermal frames | `FrameSource` (raw Y16) | synthetic °C arrays | Y16 capture → `thermal.convert.y16_to_celsius` |

## Bench bring-up checklist

When the workspace is on the Linux box:

1. **`CameraFrameSource`** — a `FrameSource` over `cv2.VideoCapture`, honoring
   UVC single-access (benchhud is the sole reader). Feed the scope panel via
   `LatestFrameReader` + `FramePump` (swap it in for `_default_scope_source`).
2. **Thermal capture** — grab the raw **Y16 192×256 @ 25 fps** stream (fourcc
   `'Y16 '`, trailing space), run `y16_to_celsius`, feed `ThermalPanel` and the
   compositor. Watch for the periodic NUC shutter stall — a brief freeze is
   normal, not an error.
3. **`V4l2LoopbackSink` live** — `sudo modprobe v4l2loopback exclusive_caps=1
   card_label=benchhud` (creates `/dev/video10` unless `video_nr` says otherwise;
   `exclusive_caps=1` is what makes OBS list it). Point `output.device` at the
   node, confirm the sink opens and OBS sees the composite; pace to the slower
   feed. If the driver adjusts the format, `open_loopback_device` raises rather
   than writing a desynced stream — match `output.width`/`height` to what it
   grants.
4. **`OwonSpm8104.open_device`** — poll on a timer, push `read_snapshot` into
   `TelemetryPanel`. 115200 baud, newline-terminated SCPI.
5. **Controls → live compose** — connect `ControlsPanel.changed` to a re-render of
   `overlay.composite.compose(scope, temps, transform, settings)` and a config
   save.
6. **Calibration** — estimate and persist the `RegistrationTransform` from
   corresponding points (thermal ↔ scope), so the bloom lands without per-session
   fiddling.

## Design rules

- **One process owns the cameras; OBS gets the composite.** UVC single-access is
  load-bearing.
- **Device-facing code is injectable.** Nothing constructs a real camera / port /
  virtual cam at import time — factories and protocols throughout.
- **Config is cv2-free.** Runtime adapters map stored names (e.g. colormap) to cv2
  constants.
- **Panels render what they're handed.** Frame/telemetry sources wire in
  separately, which is why the whole UI is testable offscreen.
- **The compositor is pure array math.** Given a scope frame, a °C array, a
  transform, and settings, it's deterministic — no hardware, no globals.
- **Kernel interfaces are packed by hand, not wrapped.** `output/v4l2.py` talks to
  v4l2loopback through stdlib `fcntl`/`struct` rather than a third-party binding.
  That keeps the struct layout under test on any OS — and keeps the licensing of
  benchhud's own output path unencumbered.
