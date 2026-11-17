# Benchmark

`scripts/bench_hud.py` runs the HUD's frame pipeline with no display, no cameras
and no v4l2loopback, for a fixed time, and says how fast it went and what it
cost. It's there so a perf change is measured the same way before and after,
instead of by hand on the live HUD. The tracking issue is
[#351](https://github.com/IamMrCupp/benchhud/issues/351).

## Run it

```bash
uv run scripts/bench_hud.py
```

That's 3 s of warm-up and 20 s measured, at the bench's settings: 1280x720
output capped at 15 fps, `reduced_live` on, thermal on, both extra feeds on.

The switches that matter on the bench:

| Switch | Config it stands for | Default |
|---|---|---|
| `--size 1280x720` | `output.width`, `output.height` | 1280x720 |
| `--fps 15` | `output.fps`, the frame cap | 15 |
| `--reduced-live` / `--no-reduced-live` | `panels.scope.reduced_live` | on |
| `--feeds` / `--no-feeds` | `output.scope_device`, `output.thermal_device` | on |
| `--thermal` / `--no-thermal` | `panels.thermal.enabled` | on |
| `--overlays` / `--no-overlays` | `telemetry_overlay` and `graph_overlay`, fed demo telemetry | off |
| `--threads 2` | `performance.opencv_threads`; 0 leaves OpenCV's one per core | the app's default, 2 |
| `--rgb-node` | the composite node answers RGB3 (a node OBS already holds), so each frame is swapped | off |

`--duration` and `--warmup` set the times. `--help` lists the rest (camera
rates, clip size, window size).

To compare two runs, save each and diff them:

```bash
uv run scripts/bench_hud.py --no-reduced-live --json before.json
```

```bash
uv run scripts/bench_hud.py --json after.json
```

```bash
uv run scripts/bench_hud.py --compare before.json after.json
```

`--compare` prints both columns and the change, and says so when the two runs
differ in config, host or clip.

On the bench there's no `uv` and no checkout. Copy the script over and run it
on the installed package:

```bash
scp scripts/bench_hud.py repair-bench:/tmp/
```

```bash
PYTHONPATH=/usr/lib/benchhud/app:/usr/lib/benchhud/app_packages python3 /tmp/bench_hud.py
```

That measures the version installed there, not the checkout the script came
from. It opens no camera and no loopback node, so it runs next to the live HUD,
but the two share the CPU: the default run measured 215% of a core with the HUD
and OBS running and 194% with the HUD stopped (0.14.0, 2026-11-16). For numbers
worth keeping, stop the HUD first and start it again after.

## What runs, and what's swapped

The script builds the real `MainWindow` on Qt's offscreen platform and starts
its preview, thermal and output loops. Decode on demand, the reduced decode, the
compose, the overlays, the panels and the loopback writer are the app's own
code. Three ends are stand-ins:

- **Scope.** A 1080p MJPEG clip, served at 30 fps by a stand-in for
  `cv2.VideoCapture` that blocks between frames like a camera. The real
  `CameraFrameSource` sits on top of it.
- **Thermal.** Synthetic Y16 frames (192x256 at 25 fps, a hot spot walking
  around) under the real `Y16Camera`.
- **Output.** The real `V4l2LoopbackSink`, writing to a device that throws the
  bytes away. The kernel's copy into the loopback isn't in the numbers.

### The clip

There's no clip in the repo. By default the script generates 30 frames in
memory at start: a blurred random texture with per-pixel noise, shifted a little
each frame, JPEG at quality 85. It's seeded, so every run on every machine
decodes the same bytes, and nothing binary has to be committed or kept in step.

A generated frame is a stand-in, not what a camera sends: its size (about
640 KiB) and its noise are its own, and decode cost follows both. For anything
that hinges on decode cost, use frames off the camera:

```bash
uv run scripts/bench_mjpeg_decode.py grab /dev/video4 1920x1080 frames-1080p/
```

```bash
uv run scripts/bench_hud.py --clip frames-1080p/
```

`grab` saves the compressed frames untouched, 30 by default. Keep them out of
the repo.

## Reading the result

```text
composite out                15.60 fps
scope feed out               15.60 fps
thermal feed out             15.60 fps
scope frames decoded         15.60 fps
thermal frames in            25.00 fps
CPU, whole process            43.0 % of one core
peak RSS                     283.0 MB

thread                               CPU %
other threads (OpenCV, Qt)            15.1
main (GUI, compose, output)           13.7
scope reader                          12.7
thermal reader                         1.5

stage                             thread            calls   ms/call  % core
scope decode                      scope reader        312     8.075    12.6
thermal read + convert            thermal reader      500     0.583     1.5
scope tick                        main                312     7.071    11.0
  scope panel                     main                312     0.410     0.6
  render composite                main                312     6.633    10.3
    scope feed                    main                312     0.772     1.2
    thermal feed colourise        main                312     1.100     1.7
    thermal feed                  main                312     0.027     0.0
    compose (blend)               main                312     2.589     4.0
    overlays                      main                312     0.007     0.0
    composite view                main                312     0.265     0.4
    output (fit + send)           main                312     0.803     1.3
thermal tick                      main                312     1.075     1.7
  thermal panel                   main                312     1.051     1.6
```

That run is from an Apple M4 Pro laptop, here to show the layout. It says
nothing about the bench.

- **Output fps** is frames written to each sink over the measured time. The cap
  is a Qt timer, and a cap of 15 measures about 15.6, here and on the live HUD.
  Well below the cap, the pipeline can't keep up. `scope frames decoded` under the
  camera's 30 is decode on demand doing its job (#346), not a drop.
- **CPU, whole process** is user plus system time over wall time, in percent of
  one core, the unit `top` uses. 250 is two and a half cores.
- **Per thread.** On Linux every thread is read from `/proc/self/task` and
  grouped by thread name. On the bench the workers all carry the process name,
  so OpenCV's and Qt's come out as one `other: python3` line. Elsewhere only the
  main thread and the two readers can be read, and the rest is one line, the
  remainder. OpenCV spreads a resize or a blend over its worker threads, so
  work that a main-thread stage asked for can show up there.
- **Stages** are wall time around each step, mean per call, and `% core` is
  that stage's share of the measured time. The indent is nesting: a parent
  includes its children, and what's left over in `render composite` is the
  reference compare and the scale down to the compose size. The scope decode
  excludes the wait for the next frame. Wall time on a loaded machine includes
  time the thread didn't get to run, so compare stages between runs on a quiet
  box, and trust the CPU lines over the stage lines when they disagree.
- **Peak RSS** is the process's high-water mark, clip generation included.
  `RSS at the end` is printed on Linux only.

The JSON has the same numbers plus the host, the versions and the settings.

## What it can't tell you

- **The bench's frame rate.** A laptop isn't a Ryzen Embedded with OBS on the
  same box. Numbers are comparable between runs on one machine, with nothing
  else changed. Bench numbers come from running it on the bench.
- **Camera and kernel cost.** USB capture, the UVC driver and the v4l2loopback
  copy aren't in it.
- **Painting to a screen.** Offscreen Qt does the pixmap work but never hands a
  frame to a compositor or a GPU.
- **Long-uptime drift.** It measures seconds after start. The live HUD is
  measured five minutes or more after start, reading the feed node OBS doesn't
  hold.

## Keeping it honest

`tests/test_bench_hud.py` runs the benchmark for about a second and checks every
field and every stage row is there. The script times the pipeline by wrapping
methods on the main window, some of them private. Rename one and that test
fails in CI, not on the bench three weeks later.
