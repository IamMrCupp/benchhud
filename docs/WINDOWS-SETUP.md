# Windows setup

Getting benchhud's composite into OBS on Windows. Linux writes to a
v4l2loopback node ([BENCH-SETUP.md](BENCH-SETUP.md)) and macOS publishes into
OBS's camera extension ([MACOS-SETUP.md](MACOS-SETUP.md)). Windows has neither,
so benchhud brings its own camera: **softcam**, a small DirectShow filter that
ships inside the installer. OBS lists it as **DirectShow Softcam**.

Cameras, the OWON and everything else are picked in Preferences, same as on the
other two. This doc covers the output side.

---

## Why softcam

A Windows program becomes a camera by registering a DirectShow filter, which is
a DLL. [softcam](https://github.com/tshino/softcam) is exactly that and nothing
more — MIT, a five-function C API, and it takes frames in the byte order OpenCV
already holds them in, so benchhud converts nothing on the way out.

The DLL isn't a binary someone handed us. CI builds it from a pinned softcam
commit (`packaging/windows/build-softcam.ps1`), and the MSI ships the one that
build produced. The C runtime is linked in, so it doesn't need the Visual C++
Redistributable to be on the machine.

**64-bit only.** benchhud and OBS are both 64-bit, and that's the DLL that
ships. A 32-bit camera app won't see it.

---

## 1. Install, and the one step that needs an administrator

Camera apps find softcam through a registration that Windows keeps for the
whole machine. Writing it takes administrator rights, once. Where that happens
depends on how you install:

- **For everyone on the PC.** The installer asks for administrator approval
  anyway, and registers the camera while it's there. Nothing more to do.
- **Just for you.** No administrator prompt, so the installer can't register
  it. Open **Preferences → Output** and press **Register virtual camera…**.
  Windows asks for approval once, and the row changes to *registered*.

Until it's registered, benchhud doesn't pretend. The status bar reads **⚠
virtual cam inactive — the Windows virtual camera isn't registered yet …** and
no frames are sent. That's deliberate: softcam will happily accept frames with
no registration, and no app could ever see them.

Uninstalling takes the registration back out.

There's a command for scripts as well:

```
benchhud.exe virtual-camera status
benchhud.exe virtual-camera register
benchhud.exe virtual-camera unregister
```

The packaged app has no console, so read the exit code: `0` means it worked
(for `status`, that the camera is registered and its DLL is there).

---

## 2. Enable output in benchhud

Tick **Enable virtual camera output** in Preferences → Output, or:

```toml
[output]
enabled = true
width = 1280    # multiples of four: softcam requires it
height = 720
```

`device` is ignored on Windows — there's one camera and no path to it.

---

## 3. Add it to OBS

Add a **Video Capture Device** source and pick **DirectShow Softcam**. If it
isn't in the list, restart OBS — softcam's own README gives the same advice for
any camera app that was open when the camera was registered.

With benchhud closed, or its output off, the camera stays in the list. It
shows black, or a dimmed copy of the last frame if benchhud has only just
stopped. That's softcam marking the source as inactive.

---

## One sender at a time

softcam takes one sender for the whole machine. If another program is already
sending to it, benchhud says **the softcam virtual camera is already in use**
and stays off. Close the other sender, then turn output off and on.

---

## Troubleshooting

| The status bar says | What to do |
|---|---|
| `the Windows virtual camera isn't registered yet …` | Preferences → Output → **Register virtual camera…**, approve the prompt, press OK. |
| `the Windows virtual camera's softcam.dll is missing …` | You're running from source, or an old install was deleted by hand and left its registration behind. Install from the MSI. |
| `the softcam virtual camera is already in use …` | Another program is sending to softcam. Close it, then toggle output. |
| `the configured output size doesn't work with the Windows virtual camera …` | Set the width and height to multiples of four. |
| Nothing wrong in benchhud, OBS shows black or a dim still | That's the camera with nothing sending. Check output is on in benchhud and the source is **DirectShow Softcam**. |

---

## What's been checked, and what hasn't

CI does this on a Windows runner for every release: builds the DLL, registers
it, has benchhud publish a known picture, and reads it back through DirectShow
the way a camera app would — the same picture, byte for byte. It then installs
the MSI, starts the installed app with output on, reads its picture the same
way, and checks that uninstalling removes the registration.

**Nobody has yet watched it in OBS on a Windows PC.** The reader in CI is
OpenCV's DirectShow backend, not OBS. Two install paths haven't run for real
either: CI installs per user from an administrator account, so neither the
all-users install nor the approval prompt behind the Preferences button has
been in front of a person. If you run it, an issue saying what you saw is
useful either way.
