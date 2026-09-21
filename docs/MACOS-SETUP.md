# macOS setup

Getting benchhud's composite into OBS on a Mac. The Linux bench uses
v4l2loopback ([BENCH-SETUP.md](BENCH-SETUP.md)); macOS has no equivalent, so
benchhud publishes into **OBS's own virtual camera** instead. There's nothing to
install beyond OBS itself, and nothing of ours to sign or approve.

You pick cameras and the OWON in Preferences, same as on Linux, with one
macOS catch: **camera access is granted per app.** Run benchhud from the `.app`
bundle ([PACKAGING.md](PACKAGING.md)), not `uv run`. Otherwise the terminal is
the one asking for the camera, and the scope quietly falls back to the
synthetic feed. The rest of this doc covers the output side.

---

## Why OBS's camera, and not our own

Since macOS 12.3 the supported way to be a camera is a **Camera Extension**;
the old DAL plug-ins are dead as of macOS 14. Shipping our own extension would
mean Swift, an `.app` in `/Applications`, Developer ID signing, notarization and
a paid Apple Developer membership — none of which survives `pip install`.

OBS already ships one, and its sink stream accepts frames from any process.
benchhud talks to it through Apple's public CoreMediaIO API via `ctypes` — no
third-party library, no compiled extension, no OBS code in our process. That
last part is what keeps it clean licensing-wise: we interoperate with OBS across
an OS boundary; we don't link to it. See the design rules in
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Install OBS and approve its extension

Install [OBS Studio](https://obsproject.com/) normally. Then the step people
miss: OBS's camera extension is **installed but not active** until macOS lets
it run.

**System Settings → General → Login Items & Extensions → Camera Extensions**
— turn on OBS's. If it isn't listed, launch OBS once and hit **Start Virtual
Camera** in the Controls dock; macOS will prompt to allow the extension. Then
stop it again — see the single-writer note below.

That's it for the OS side. If you skip this, benchhud tells you exactly this in
the status bar rather than silently streaming nothing.

---

## 2. Enable output in benchhud

Tick **Enable virtual camera output** in Preferences → Output, or:

```toml
[output]
enabled = true
width   = 1280
height  = 720
```

`output.device` is a Linux thing (the loopback node path) — it's ignored on
macOS. Width and height are the frame size benchhud publishes.

---

## 3. Verify

Launch benchhud. With output enabled and OBS's extension approved, the status bar
shows no warning; if it shows **⚠ virtual cam inactive — …**, read the reason
(table below).

In OBS, add a **Video Capture Device** source and pick **OBS Virtual Camera**.
The benchhud composite appears. Frames flow only while benchhud is running with
output enabled.

---

## Single writer

**While benchhud is publishing, don't press Start Virtual Camera in OBS.** The
extension has one sink stream; benchhud is holding it. OBS's own button would try
to take the same stream. In this workflow OBS is the *consumer* — it reads the
virtual camera as a source and does its own thing with it — not a second
producer.

If OBS's Start Virtual Camera was already running when benchhud launched, stop it
in OBS and toggle output off/on in benchhud's Preferences.

---

## Troubleshooting

| Status-bar reason | Cause / fix |
|---|---|
| `OBS Virtual Camera not found — install OBS, then approve its camera extension in System Settings → General → Login Items & Extensions` | Exactly that. Usually the extension is installed but unapproved (step 1). |
| A reason mentioning the sink stream failing to open | OBS's extension is present but the stream wouldn't start. Most likely OBS's own Start Virtual Camera is holding it — stop it, then toggle benchhud output. |
| Output enabled, no warning, but OBS shows a black or frozen source | Check OBS is reading **OBS Virtual Camera**, not a real camera; and that OBS's own Start Virtual Camera is off. |
| No warning, output disabled | Nothing's wrong — deliberately-off output isn't a warning. Tick **Enable virtual camera output**. |

---

## Not covered

- **Windows.** No backend yet — the status bar says so. Tracked as a committed
  target, not a maybe.
- **Other virtual-camera apps** (CamTwist, mmhmm, etc.). benchhud targets OBS's
  extension specifically. Another extension whose sink accepts external frames
  would be a small addition to `output/cmio.py`; none is planned.
