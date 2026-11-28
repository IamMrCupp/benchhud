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
  Reads are lenient (a wrong type or shape falls back to the default); a file
  that isn't TOML raises `ConfigError`, and `shell/config_problem` turns that
  into "start on defaults, say so, save nothing over the file". A save is read
  back before the file is touched.
- **`shell/`** — `MainWindow` (dockable panel host + preview loop), `PanelManager`,
  the `Panel` base, `CompositeView` (the centre pane's live composite),
  `Preferences` (device dropdowns, tools editor, intake + boards settings),
  `qt_image` (BGR→QImage), `theme` / `fonts` / `icon` / `about`, the dialogs
  `ticket_picker`, `engagement_picker`, `board_dialogs` (Find board / Boards
  to source) and `chip_dialog` (**Chips → Find chip…**: one kept, non-modal
  window; the main window hands it the chip source, an opener over the
  datasheet library and "may this link out now?", and tells it when
  Preferences changes either; `browser` is the one door an address leaves
  by), and the panels: `scope`, `thermal`, `power` (the styled meter),
  `controls`, `launcher`, `jobs`, `inventory` (with a chip source configured,
  the selected part's MPN is looked up on a worker — in the on-air chip
  sources only, like the chip card — and the row shows the
  chip's reference details and **Open datasheet**; the main window builds
  the datasheet library from `[chips] datasheet_dir` and lists it off the
  GUI thread), `terminal` (hosts the xterm.js
  page in QtWebEngine, created only when the terminal is on), `engagement` (the
  active bench-recon engagement, off air), `interfaces` (connected probes/
  adapters cross-mapped to assigned assets, with a manual port→asset bind),
  `pinout` (a static JTAG/SWD/UART/CAN/SPI/I²C crib beside the engagement,
  stream-safe), `chip` (one chip from the chip reference as a card — part
  number, maker, package, function, marks — looked up off the GUI thread in
  the on-air chip sources only, stream-safe), `captures` (firmware-dump / capture artifact metadata by ref,
  off air), plus `telemetry` (superseded by
  `power`, class retained).
- **`capture/`** — `FrameSource` protocol, `SyntheticFrameSource`,
  `CameraFrameSource` (cv2), threaded `LatestFrameReader` (latest-frame buffer),
  `FramePump` (Qt-free dispatch).
- **`output/`** — `OutputSink` protocol, `FakeSink`, `NullSink` (carries a
  `reason` the status bar shows), and one real backend per platform, chosen by
  `open_output_sink`: `V4l2LoopbackSink` (`v4l2.py`: writes frames straight to a
  v4l2loopback node via `VIDIOC_S_FMT` + `os.write`, stdlib only; it asks for
  BGR24, the frames' own byte order, and swaps to RGB24 only when the node
  answers with that) on Linux, written from its own thread by `PacedSink`
  (`paced.py`: the GUI thread only hands over the newest frame, and the last
  one is written again whenever a new one is late, so OBS never times out on
  the node while the GUI thread is held up, #566),
  `CmioSink` (`cmio.py`: publishes UYVY frames into OBS's camera-extension sink
  stream through Apple's public CoreMediaIO API via `ctypes` — no PyObjC, no
  compiled extension) on macOS, and `SoftcamSink` (`softcam.py`: sends BGR24
  straight into softcam's DirectShow filter DLL via `ctypes` — the one backend
  that converts nothing) on Windows. It loads the DLL Windows has registered
  and refuses to open without a registration, since softcam would take the
  frames and no camera app could see them; `softcam_register.py` does the
  registering (`regsvr32`, elevated) and is `benchhud virtual-camera`. `onair.py` holds the tagged-sensitive
  model (`Layer` + `compose_on_air`/`compose_preview`) that keeps sensitive
  surfaces out of the published frame — see the stream-safety design rule below.
- **`browser_sources/`** — the data panels as OBS Browser sources: `hub`
  (`TopicHub`, the latest payload per panel, handed from the GUI thread to the
  server's), `payloads` (meter / graph / job-card dicts built from the same
  objects the docks draw; the job card is a field whitelist), and `server`
  (`BrowserSourceServer`, stdlib `ThreadingHTTPServer` on `127.0.0.1`, one page
  per panel plus a Server-Sent Events feed). The pages live in
  `resources/web/`. See [BROWSER-SOURCES.md](BROWSER-SOURCES.md). `control`
  is the one write path: job control for `benchhud job …` (`cli_job`, Qt-free),
  token-gated, with each command queued onto the GUI thread by a signal. See
  [JOBS.md](JOBS.md#from-a-terminal).
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
  `resolve_capture_device` (Qt device id → OpenCV index), and
  `list_usb_interfaces` (`UsbInterface` with VID:PID / serial for the interface
  monitor's asset cross-map).
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
- **`assets/`** — the bench-parts durable-asset read client (gear you assign to
  an engagement, not consumables): `source` (`Asset`, `AssetSource`, null /
  in-memory), `http_source` (`/api/assets` over stdlib `urllib`, reads degrade,
  read-only). Shares the bench-parts connection with `inventory/`; the recon
  interface monitor cross-maps attached ports to assets by `assigned_ref`.
- **`recon/`** — the bench-recon client seam, same shape as `tickets/`: `source`
  (`Engagement` / `Artifact` ref-safe projection types, `ReconSource`, null /
  in-memory), `http_source` (the bench-recon API over stdlib `urllib`, reads
  degrade), `importer` (`resolve_engagement` — "Import from engagement…" makes a
  ref the active engagement; benchhud stores only the ref, and "End
  engagement…" forgets it again, which is what turns Find chip's web lookups
  back on). Sensitive detail
  stays in bench-recon; the recon panels are off air by default (`output.onair`).
- **`acceptance.py`** — the Track E live acceptance harness (`scripts/track_e_acceptance.py`
  is the CLI). Runs against the live bench-recon + bench-parts services and reports,
  pass/fail: the on-air enforcement proof (a sensitive layer's bytes never reach the
  sink frame), the panel sensitivity policy, the ref-safe projection boundary (the
  live API returns only whitelisted fields), and a preflight. Read-only — it verifies
  an existing engagement ref and never writes to a system of record. The network goes
  through an injectable `Probe`, so `tests/test_acceptance_track_e.py` covers the
  assertion logic with no socket.
- **`boards/`** — the local board-doc library: `library` (`BoardLibrary` —
  recursive scan keyed by filename stem, resolve / search / missing, schematics
  kept distinct from board views), `opener` (`find_board_viewer` from the Tools
  entries + `open_board` / `open_schematic`, OS-handler fallback), `worklist`
  (`boards_to_source` — open tickets minus the library, `customer`-free). Pure
  path logic; nothing renders. See [BOARDS.md](BOARDS.md).
- **`chips/`** — the chip reference seam (#452), same shape as `inventory/` but
  read-only: `model` (`Chip` and its `Marking` / `Document` / `Substitute` /
  `UsedIn` records, field for field one chip in chip-data's `index.json`;
  identity is the full part number, case ignored), `marking` (`match_markings`
  — a typed top mark to ranked candidates, never one answer), `source`
  (`ChipSource`, null / in-memory), `dir_source` (`DirChipSource` — a chip-data
  checkout, a build of one, or a private overlay: the built `index.json` when
  it's there and no older than the chip files, else the `chips/**/*.toml`
  files with `package_family` and replaced function slugs worked out from the
  checkout's `vocab/`; read on first lookup and kept, `reload()` to re-read;
  a bad file is skipped with a log line), `composite` (`CompositeChipSource`
  — several sources in order of precedence as one: the first source with a
  single value wins it, a later one that disagrees is kept as a `Conflict`,
  lists are joined; `merged()` gives a `MergedChip` saying which source each
  part came from, while `get` / `by_marking` still return plain chips),
  `builder` (`build_chip_sources` — the `[chips]` config's ordered
  `[[chips.sources]]` to two views of the same sources: `everything` for
  Find chip, and `on_air`, a second composite over the entries not marked
  `on_air = false`, which is all the HUD window — the Chip panel and the
  inventory row — is handed; the null
  source when there are none; `warm` hands the first read to a worker
  thread). The
  main window builds it at start and after Preferences; Preferences → Chips
  edits the list and shows what each folder loaded, read on a worker.
  `datasheets` (`DatasheetLibrary` — the folder of documents, found by
  SHA-256 first and by name second, hashed on demand), `opening` (finding
  and opening the file held for a chip's document, and the one set of words
  for "exact copy" versus "name match" that the inventory row and Find chip
  share), `find` (`find_chips` — what was typed, tried as a mark and as a part
  number, as one list; `with_provenance` hangs each candidate's `MergedChip`
  on it), `linkout` (the address for a mark on a marking site, and the check
  a document's own address has to pass). Pure Python below the Qt code; no
  network, no git. See [CHIPS.md](CHIPS.md).
- **`launcher/`** — `ToolLauncher` (subprocess) + `catalog` (configured tools).

## Measuring it

`scripts/bench_hud.py` runs this pipeline headless — the real `MainWindow`
offscreen, a generated 1080p MJPEG clip for the scope, synthetic Y16 for the
thermal, the loopback sink writing to nowhere — and reports output fps, CPU per
thread, time per stage and peak RSS, as a table and as JSON. Run it before and
after a perf change. See [BENCHMARK.md](BENCHMARK.md).

## The seams

Every hardware boundary is a protocol with a tested fake. The bench work is
implementing the real side of each — no consumer changes.

| Seam | Protocol | Tested with | Bench implementation |
|---|---|---|---|
| Camera in | `capture.FrameSource` | `SyntheticFrameSource` | `CameraFrameSource` over `cv2.VideoCapture(/dev/videoN)` |
| Virtual cam out (Linux) | `output.OutputSink` | `FakeSink`, fake `V4l2Device` | `V4l2LoopbackSink` on a real `/dev/videoN` loopback node |
| Virtual cam out (macOS) | `output.OutputSink` | pure helpers (fourcc, stream selection, BGR→UYVY) + injected fake enumeration | `CmioSink` into OBS's camera extension; the real-delivery test runs only on a Mac with the extension approved |
| Virtual cam out (Windows) | `output.OutputSink` | fake camera (byte layout), injected registry and elevated run | `SoftcamSink` into the registered softcam.dll; CI's `softcam` job reads the picture back through DirectShow |
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
  Board and device identifiers carry no personal data and are safe anywhere. The
  terminal has a second on-air path — its read-only view can be *shared* to OBS — so
  the same rule applies there: a raw serial console (a device-under-test UART) is
  sensitive and is never shared, even with the share switch on; only the operator's
  shell and the query-only OWON console may go on air.
