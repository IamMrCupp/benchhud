# Architecture

benchhud is a HUD shell that hosts panels, wired to a capture → composite →
output pipeline. Everything device-facing sits behind a small protocol, so the
whole thing runs and is tested without hardware — the real devices drop in on the
bench without touching any consumer.

Alongside the video path runs a second, quieter one: the **repair log** — a
local-first job store the pipeline feeds, so a capture or an over-current trip
lands in the job timeline as you work. Same rule applies, one layer out — a shop
ticket system is a protocol with a fake, not a dependency.

## Module map

- **`app.py`** — entry point: loads config, builds the `QApplication`, opens the
  shell, starts the preview loop. `--version` stays Qt-free (works headless).
- **`config.py`** — typed config tree + TOML load/save. Deliberately cv2-free.
- **`shell/`** — `MainWindow` (dockable panel host + preview loop), `PanelManager`,
  the `Panel` base, `CompositeView` (the centre pane's live composite),
  `Preferences` (device dropdowns, tools editor, intake + boards settings),
  `qt_image` (BGR→QImage), `theme` / `fonts` / `icon` / `about`, the dialogs
  `ticket_picker`, `engagement_picker` and `board_dialogs` (Find board / Boards
  to source), and the panels: `scope`, `thermal`, `power` (the styled meter),
  `controls`, `launcher`, `jobs`, `inventory`, `terminal` (hosts the xterm.js
  page in QtWebEngine, created only when the terminal is on), `engagement` (the
  active bench-recon engagement, off air), plus `telemetry` (superseded by
  `power`, class retained).
- **`capture/`** — `FrameSource` protocol, `SyntheticFrameSource`,
  `CameraFrameSource` (cv2), threaded `LatestFrameReader` (latest-frame buffer),
  `FramePump` (Qt-free dispatch).
- **`output/`** — `OutputSink` protocol, `FakeSink`, `NullSink` (carries a
  `reason` the status bar shows), and one real backend per platform, chosen by
  `open_output_sink`: `V4l2LoopbackSink` (`v4l2.py`: writes RGB24 straight to a
  v4l2loopback node via `VIDIOC_S_FMT` + `os.write`, stdlib only) on Linux,
  `CmioSink` (`cmio.py`: publishes UYVY frames into OBS's camera-extension sink
  stream through Apple's public CoreMediaIO API via `ctypes` — no PyObjC, no
  compiled extension) on macOS, and `SoftcamSink` (`softcam.py`: sends BGR24
  straight into softcam's DirectShow filter DLL via `ctypes` — the one backend
  that converts nothing) on Windows. `onair.py` holds the tagged-sensitive
  model (`Layer` + `compose_on_air`/`compose_preview`) that keeps sensitive
  surfaces out of the published frame — see the stream-safety design rule below.
- **`browser_sources/`** — the data panels as OBS Browser sources: `hub`
  (`TopicHub`, the latest payload per panel, handed from the GUI thread to the
  server's), `payloads` (meter / graph / job-card dicts built from the same
  objects the docks draw; the job card is a field whitelist), and `server`
  (`BrowserSourceServer`, stdlib `ThreadingHTTPServer` on `127.0.0.1`, one page
  per panel plus a Server-Sent Events feed). The pages live in
  `resources/web/`. See [BROWSER-SOURCES.md](BROWSER-SOURCES.md).
- **`terminal/`** — the in-app terminal (#194): `pty_session` (`PtySession`, a
  shell in a POSIX pseudo-terminal, read on its own thread), `host`
  (`TerminalHost`: bounded scrollback by absolute position, size/exit/share
  state, the per-run write token and the persisted share token, the pause
  floor), and `events` (`terminal_events`, the rules for what a page, and
  especially the stream view, receives). The page is
  `resources/web/terminal.html` on vendored xterm.js, served by
  `browser_sources/server` and shown in-app by `shell/panels/terminal`. See
  [TERMINAL.md](TERMINAL.md).
- **`instruments/`** — `SerialPort` / `LineInstrument` seam, `OwonSpm8104` driver,
  `read_snapshot` (resilient telemetry), `PowerHistory` (rolling V/A/W buffer for
  the trend chart).
- **`devices/`** — camera + serial enumeration behind a `DeviceScanner` protocol
  (`/dev/video*` + `/sys` names on Linux, Qt fallback, pyserial `list_ports`),
  and `resolve_capture_device` (Qt device id → OpenCV index).
- **`thermal/`** — `convert` (Y16→°C, Sipeed spec), `analyze` (hotspot / threshold
  / stats).
- **`overlay/`** — `RegistrationTransform` (affine warp), `composite`
  (colorize / blend / tint / marker / `compose`), `telemetry` (burns the meter
  into the streamed frame), `config_adapter`.
- **`jobs/`** — the local-first repair log: `store` (SQLite jobs + note
  timeline), `capture` (bench state → image + summary note), `fault`
  (over-current edge detector), `report` (Markdown export). See
  [JOBS.md](JOBS.md).
- **`tickets/`** — the shop-ticket client seam: `source` (`Ticket`,
  `TicketSource`, null / in-memory), `http_source` (the intake API over stdlib
  `urllib`), `importer` (idempotent ticket → job, backfills `board_id`). See
  [INTAKE.md](INTAKE.md).
- **`recon/`** — the bench-recon client seam, same shape as `tickets/`: `source`
  (`Engagement` / `Artifact` ref-safe projection types, `ReconSource`, null /
  in-memory), `http_source` (the bench-recon API over stdlib `urllib`, reads
  degrade), `importer` (`resolve_engagement` — "Import from engagement…" makes a
  ref the active engagement; benchhud stores only the ref). Sensitive detail
  stays in bench-recon; the recon panels are off air by default (`output.onair`).
- **`boards/`** — the local board-doc library: `library` (`BoardLibrary` —
  recursive scan keyed by filename stem, resolve / search / missing, schematics
  kept distinct from board views), `opener` (`find_board_viewer` from the Tools
  entries + `open_board` / `open_schematic`, OS-handler fallback), `worklist`
  (`boards_to_source` — open tickets minus the library, `customer`-free). Pure
  path logic; nothing renders. See [BOARDS.md](BOARDS.md).
- **`launcher/`** — `ToolLauncher` (subprocess) + `catalog` (configured tools).

## The seams

Every hardware boundary is a protocol with a tested fake. The bench work is
implementing the real side of each — no consumer changes.

| Seam | Protocol | Tested with | Bench implementation |
|---|---|---|---|
| Camera in | `capture.FrameSource` | `SyntheticFrameSource` | `CameraFrameSource` over `cv2.VideoCapture(/dev/videoN)` |
| Virtual cam out (Linux) | `output.OutputSink` | `FakeSink`, fake `V4l2Device` | `V4l2LoopbackSink` on a real `/dev/videoN` loopback node |
| Virtual cam out (macOS) | `output.OutputSink` | pure helpers (fourcc, stream selection, BGR→UYVY) + injected fake enumeration | `CmioSink` into OBS's camera extension; the real-delivery test runs only on a Mac with the extension approved |
| Instrument | `instruments.SerialPort` | `FakeSerialPort` | `OwonSpm8104.open_device('/dev/ttyUSB0')` |
| Thermal frames | `FrameSource` (raw Y16) | synthetic °C arrays | Y16 capture → `thermal.convert.y16_to_celsius` |
| Device discovery | `devices.DeviceScanner` | canned `/sys` + `list_ports` fakes | `SystemDeviceScanner` over real `/dev` nodes |
| Ticket queue | `tickets.TicketSource` | `InMemoryTicketSource`, injected opener | `HttpTicketSource` against the intake API |
| Recon engagements | `recon.ReconSource` | `InMemoryReconSource`, injected opener | `HttpReconSource` against the bench-recon API |
| Board library | `boards.BoardLibrary` + injected `os_open` | `tmp_path` libraries, fake opener | A real `library_dir`; viewer via the Tools entries or the OS handler |

## Bench bring-up checklist

The developer-facing view of the remaining work. For the operator's step-by-step
(install, identify devices, load v4l2loopback, configure, verify), see
[BENCH-SETUP.md](BENCH-SETUP.md).

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
- **OS interfaces are called directly, not wrapped.** `output/v4l2.py` talks to
  v4l2loopback through stdlib `fcntl`/`struct`; `output/cmio.py` talks to
  CoreMediaIO and `output/softcam.py` to softcam's DLL, both through `ctypes`.
  No third-party binding on any of the three paths. That keeps
  the pure parts (struct layout, pixel conversion, stream selection) under test on
  any OS — and keeps the licensing of benchhud's own output path unencumbered.
- **Interoperating with OBS is not linking to it.** The GPL problem that got
  `pyvirtualcam` removed was *linking* a GPLv2 library into this process. The
  macOS backend calls only Apple's public frameworks and hands frames to OBS's
  separately-installed camera extension across an OS IPC boundary — the same
  relationship as writing to a v4l2loopback node on Linux. Keep it that way: no
  OBS code, no OBS libraries, in-process. Windows takes the same posture for the
  same reason, which is why it goes through softcam (MIT) rather than OBS's own
  shared-memory protocol — that protocol's only public spec is GPL source.
- **The bench log is local-first.** Jobs live in a SQLite file beside the config —
  no server, no account, works with the network down. A shop ticket system is an
  optional source layered on top, never a dependency.
- **Nothing off-bench raises into the HUD.** An unreachable intake, a bad token, a
  malformed payload — each degrades to an empty queue plus a log line. A flaky LAN
  can't take the bench down mid-repair.
- **Sensitive surfaces never reach the composite.** The output is a stream surface,
  so anything tagged sensitive — the ticket customer, and the recon set (target
  identity, client, credentials, findings, raw console text) — renders only in docks
  and dialogs, never in the frame handed to the sink. The rule is a tag, not a special
  case: a surface offered to the compositor is a `Layer` with a `sensitive` flag
  (`benchhud.output.onair`), and panels carry `Panel.sensitive`. `_show_composite`
  draws every layer on the local preview but `compose_on_air` drops the sensitive ones
  from the published frame. The default is safe for the bench panels and sensitive for
  recon panels, so a new recon panel is off air until someone decides it is stream-safe;
  `tests/test_onair.py` asserts a tagged surface's bytes never reach the output sink.
  Board and device identifiers carry no personal data and are safe anywhere.
