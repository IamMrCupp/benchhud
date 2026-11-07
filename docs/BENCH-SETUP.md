# Bench setup (Linux)

Bringing benchhud up on the repair bench: identify the cameras and the
instrument, create the virtual-camera output OBS reads, and point the config at
them. Linux is the reference bench, so this is the most-tested path. On a Mac
the cameras and instrument work the same but output goes into OBS's own virtual
camera instead of a loopback node — see [MACOS-SETUP.md](MACOS-SETUP.md). Windows
has no output backend yet.

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

### Cameras keep their names

`/dev/videoN` numbers are handed out in plug-in order, so a camera that drops off
USB and comes back can return under another number. benchhud saves each camera
by its stable name instead, for example
`/dev/v4l/by-id/usb-Sipeed_Ltd._Thermal_Camera__UVC__0123456789-video-index0`.
That name follows the camera, so after a drop the scope or thermal reconnects by
itself within a few seconds. The status bar says *reconnecting* meanwhile.
Configs with a plain `/dev/videoN` are converted on start.

### Keep the thermal camera awake

Linux suspends idle USB devices after two seconds, and some thermal cameras
(the Sipeed T256s among them) don't wake cleanly; they reset and drop off the
bus. Turn autosuspend off for it:

```bash
echo 'ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="359f", ATTR{idProduct}=="4201", TEST=="power/control", ATTR{power/control}="on"' \
  | sudo tee /etc/udev/rules.d/60-benchhud-thermal.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Check how often it has dropped since boot with
`journalctl -k -b | grep -c "USB disconnect"`. For a different camera, take its
`idVendor:idProduct` from `lsusb`.

The T256s also pauses for a second or two while its shutter recalibrates, and
it crashes if it's restarted during one. benchhud holds the last frame (dimmed,
marked "paused") through a pause, and waits 10 s before deciding the camera's
gone. Reconnect attempts then back off from 5 s to a minute.

If your hub can switch its ports (`uhubctl` lists it with `ppps`), benchhud can
reset a camera that stays gone. Find the hub and port with `sudo uhubctl`, let
your account run it without a password, and set it in the config:

```bash
echo "$USER ALL=(root) NOPASSWD: /usr/sbin/uhubctl" | sudo tee /etc/sudoers.d/benchhud-uhubctl
```

```toml
[panels.thermal]
reset_port = "1-4:4"
```

After 30 s gone, the port is cycled once. Some hubs advertise switching but
only cut the data lines; that's still enough to reset a hung camera.

### Drop torn frames

When part of a frame goes missing on USB, Linux's camera driver (`uvcvideo`)
can drop the frame or hand it over incomplete. Some kernels (7.0 on the
reference bench) hand it over: the thermal image then tears, with a band of
rows shifted out of place, most often while the OWON is being read on the same
hub. Have the driver drop them instead:

```bash
echo "options uvcvideo nodrop=0" | sudo tee /etc/modprobe.d/uvcvideo-nodrop.conf
echo 0 | sudo tee /sys/module/uvcvideo/parameters/nodrop
```

The first line keeps it across reboots; the second applies it now. Restart
benchhud so the cameras reopen with it. A dropped frame just holds the last one
on screen a moment longer. benchhud says so in the status bar while the driver
still passes torn frames on.

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

Make it load on boot. Without this, the first thing to load the module after a
reboot decides its options, and that's often OBS (below):

```bash
echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf
printf 'options v4l2loopback exclusive_caps=1 card_label=benchhud video_nr=10\n' \
  | sudo tee /etc/modprobe.d/benchhud-loopback.conf
```

### OBS loads the module too

OBS's **Start Virtual Camera** runs `modprobe v4l2loopback` with its own options
when the module isn't loaded yet. If OBS gets there first, the only node is
OBS's (`/dev/video5` "OBS Virtual Camera", say). Options only apply when the
module loads, so `video_nr=10` never happens. benchhud then says `/dev/video10
doesn't exist` and lists the nodes that do.

If you also use OBS's own virtual camera (for Zoom or Discord), give each one a
node. Quit OBS, then:

```bash
sudo modprobe -r v4l2loopback
sudo modprobe v4l2loopback devices=2 video_nr=10,11 \
  card_label=benchhud,OBS-Virtual-Camera exclusive_caps=1,1
printf 'options v4l2loopback devices=2 video_nr=10,11 card_label=benchhud,OBS-Virtual-Camera exclusive_caps=1,1\n' \
  | sudo tee /etc/modprobe.d/benchhud-loopback.conf
```

benchhud writes `/dev/video10`, and OBS's Start Virtual Camera takes the other
one.

### When it still won't load

- **`Key was rejected by service`:** Secure Boot is blocking the unsigned DKMS
  module. Reboot and finish the MOK enrollment screen the package set up, or
  turn Secure Boot off.
- **`Module v4l2loopback not found` after a kernel update:** rebuild it with
  `sudo dpkg-reconfigure v4l2loopback-dkms`.

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
5. **Line the thermal up with the scope.** The thermal starts centred and
   filling the view (scale 1.0). The quick way is **Calibrate…** in the Controls
   dock:
   1. Touch a hot iron tip to the board and click the tip in the main view.
   2. Move the tip well away (opposite corners of the scope's view work best)
      and click it again.

   benchhud finds the tip in the thermal image itself and works out the scale,
   rotation and offset. **Undo** goes back, and Esc cancels halfway. Fine-tune
   with the Scale and Offset fields if you like; it's all saved for next time.

   The thermal camera usually sees far more than the microscope, so expect a
   scale well above 1. Changing the scope's focus height shifts the alignment a
   little (the cameras look from different angles), so calibrate at your working
   height.

   - **Use the thermal's macro lens for board work.** The bare lens sees a whole
     room and goes soft a few centimetres away. The macro lens narrows the view
     to a patch of board, so far more of its 256×192 pixels land on the parts
     you're probing, and it focuses close. Mount the camera at the lens's
     working distance, and calibrate again after fitting or removing it.
   - **A portrait thermal on a landscape scope** (the T256s is 192×256): use ⟲
     / ⟳ beside Rotation to turn it 90° at a time. The fit follows the turn, so
     it still fills the view at Scale 1.000. Calibrate… works out the turn by
     itself.
   - **Fine-tuning by hand:** Scale steps 0.005 and Rotation 0.1°. A small
     rotation tweak doesn't change the size, so set rotation and scale
     independently. Click into a box before using the mouse wheel on it;
     scrolling past the Controls panel leaves the values alone.

Once the hardware's up, the bench-workflow docs take over: [JOBS.md](JOBS.md)
for the repair log (captures, automatic fault logging, reports) and
[INTAKE.md](INTAKE.md) if you're pulling tickets from a shop intake system.

---

## 6. Controlling the supply (optional)

By default benchhud only **reads** the OWON. The Meter panel's **Supply** section
can also set the voltage and current limit, switch the output and change the
DMM mode, once you turn control on:

```toml
[instruments]
control = true
max_voltage = 5.5   # hard ceilings, enforced by the driver
max_current = 1.0
```

- **Output ON asks first**, naming the setpoints, and *No* is the default, so
  Enter can't energize a rail.
- **Output OFF always works,** even with control off.
- The spin boxes stop at the ceilings, and the driver refuses anything over them
  regardless.
- A command that fails is shown and **never retried**; decide yourself whether
  to send it again.
- benchhud turns the output off when it connects, disconnects and quits, and
  never inherits a live rail.

Voltage, current limit, output on/off and the DMM mode were verified on the
reference bench (#164, #283). The SPM8104's DMM has eight modes: DC and AC
volts, DC and AC amps, resistance, continuity, diode and capacitance.

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
