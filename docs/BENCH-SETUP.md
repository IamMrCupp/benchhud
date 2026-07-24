# Bench setup (Linux)

Bringing benchhud up on the repair bench: identify the cameras and the
instrument, create the virtual-camera output OBS reads, and point the config at
them. Linux-first — this is the tested platform. macOS/Windows run the app but
have no virtual-camera output yet (see [ARCHITECTURE.md](ARCHITECTURE.md)).

The verified reference bench (Ubuntu 26.04): a Sipeed T256s thermal camera, a
BTER 4K scope camera, and an OWON SPM8104 combined PSU/DMM. Your device numbers
and model names will differ — identify them (step 2) rather than copying paths.

---

## 1. Prerequisites

- **Ubuntu 26.04** (or similar). Root/sudo for the kernel module and udev.
- **[uv](https://docs.astral.sh/uv/)** for the Python environment.
- Your user in the **`dialout`** (serial), **`video`** (V4L2), and **`plugdev`**
  groups. Check and, if needed, add — then log out and back in:

  ```bash
  groups
  sudo usermod -aG dialout,video,plugdev "$USER"
  ```

- Clone and sync:

  ```bash
  git clone https://github.com/IamMrCupp/benchhud.git
  cd benchhud
  uv sync
  ```

---

## 2. Identify the devices

Cameras enumerate as `/dev/videoN`. A single UVC camera usually claims **two**
nodes (video + metadata); the even one is normally the capture node. List them
with their model names:

```bash
v4l2-ctl --list-devices
```

On the reference bench this shows the **scope** (BTER 4K, `YW3609A`) at
`/dev/video4` and the **thermal** camera (Sipeed T256s, `Thermal Camera (UVC)`)
at `/dev/video6`. Note *your* numbers.

Confirm the thermal camera exposes the raw **Y16** format benchhud needs for real
per-pixel °C (note the **trailing space** in the fourcc — it's significant):

```bash
v4l2-ctl -d /dev/video6 --list-formats-ext | grep -A3 "'Y16 '"
```

Expect **192×256 @ 25 fps**. (256×192 is the native sensor res; 480×640 is
upscaled with no extra detail — use 192×256.)

The **OWON** appears as a USB-serial device (CH341/CH340 bridge). Find it:

```bash
ls -l /dev/serial/by-id/          # stable path, survives re-plug
dmesg | grep -i ch34              # shows which ttyUSBn it attached as
```

It's `/dev/ttyUSB0` on the reference bench, **115200 baud**. Sanity-check it
talks SCPI at 115200 8N1 — `*IDN?\n` should return `OWON,SPM8104,...`.

Smoke-test a camera device directly (which node is scope vs thermal):

```bash
uv run scripts/check_camera.py /dev/video4   # -> device=... open=True size=(w, h)
```

> **UVC single-access:** each camera allows exactly one reader. benchhud must be
> the sole reader — OBS gets the composited loopback output, never the raw cams.
> If a cam "can't open," something else (a browser tab, a prior benchhud, OBS
> pointed at the wrong device) is holding it.

---

## 3. Create the virtual-camera output

benchhud writes its composite to a **v4l2loopback** node that OBS then reads as a
normal camera. Install and load the module:

```bash
sudo apt install v4l2loopback-dkms v4l2loopback-utils
sudo modprobe v4l2loopback exclusive_caps=1 card_label=benchhud video_nr=10
```

- **`exclusive_caps=1`** is what makes OBS (and most apps) list the node — omit
  it and the device often won't appear as a capture source.
- **`video_nr=10`** pins it to `/dev/video10` so the config path is stable;
  otherwise the module grabs the next free number. Pick one that isn't a real
  camera.

Confirm it exists:

```bash
v4l2-ctl --list-devices | grep -A1 benchhud   # -> /dev/video10
```

Make it load on boot (optional):

```bash
echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf
printf 'options v4l2loopback exclusive_caps=1 card_label=benchhud video_nr=10\n' \
  | sudo tee /etc/modprobe.d/benchhud-loopback.conf
```

---

## 4. Point the config at the devices

benchhud writes its live config per-OS (or set `$BENCHHUD_CONFIG`). You can edit
the devices in **Preferences** once it's running, or seed the file from
[`config.example.toml`](../config.example.toml). The bench-relevant stanzas:

```toml
[output]
enabled = true
device  = "/dev/video10"   # the loopback node from step 3
width   = 1280
height  = 720
fps     = 15               # pace to the slower feed

[panels.scope]
enabled = true
device  = "/dev/video4"    # your scope cam

[panels.thermal]
enabled = true
device  = "/dev/video6"    # your thermal cam

[panels.telemetry]
enabled = true
port    = "/dev/ttyUSB0"   # the OWON
baud    = 115200
```

> Match `output.width`/`height` to what the loopback grants. benchhud asks the
> device for that size and **refuses to stream if the driver adjusts it**
> (rather than silently sending desynced frames) — if output won't open, check
> the sizes agree.

---

## 5. First run and verify

```bash
uv run benchhud
```

Bring-up checklist:

1. **Shell opens** with its docks (scope · thermal · meter · controls · tools ·
   jobs). A camera that won't open falls back to the synthetic feed rather than
   failing — so if the scope dock shows the animated placeholder, the device
   didn't open (wrong path, busy, or permissions).
2. **Scope + thermal show live frames.** The centre pane renders the composite.
3. **Meter reads the OWON** — V/A/W and the DMM header update.
4. **OBS sees `benchhud`** — add a Video Capture Device source and pick the
   loopback node. The composite should appear and update smoothly.
5. **Register the thermal onto the scope** — with scale 1.0 / offset 0,0 the
   thermal lands in the frame's top-left corner. Nudge scale/offset in the
   Controls dock until the bloom sits on the component; it's saved for next time.

Once the hardware's up, the bench-workflow docs take over: [JOBS.md](JOBS.md)
for the repair log (captures, automatic fault logging, reports) and
[INTAKE.md](INTAKE.md) if you're pulling tickets from a shop intake system.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Scope/thermal dock shows the animated placeholder | Device didn't open — wrong `/dev/videoN`, another reader holds it (UVC single-access), or not in the `video` group. |
| OBS doesn't list `benchhud` | Module loaded without `exclusive_caps=1`. Re-`modprobe` with it. |
| Output logs "unavailable" and falls back | Loopback node missing/wrong path, or another writer holds it. benchhud keeps running without the OBS feed — fix `output.device`. |
| Output logs the driver "adjusted the format" | `output.width`/`height` don't match what the loopback grants. Set them to the granted size. |
| Thermal image briefly freezes | The Sipeed's periodic **NUC shutter** — normal, not an error. |
| No `/dev/ttyUSB0` | CH34x driver/permissions — check `dmesg | grep ch34` and `dialout` membership. |
| Frames stutter under load | All cams share one USB 2.0 bus (480M) on the reference bench — isochronous bandwidth is finite. Prefer the small 192×256 Y16 thermal; consider moving the scope cam to a USB 3 port. |
