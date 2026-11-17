# Terminal

A real terminal inside benchhud: your own shell, in a panel beside the scope and
the meter. Flash a board, tail a log or poke a serial tool without leaving the HUD.
The same terminal can also go on stream as an OBS Browser source, output only.

macOS, Linux and Windows. The Windows backend (ConPTY) is new and hasn't been run
on a Windows bench yet: see [Windows](#windows).

## Turning it on

Preferences → **Terminal** → *Show a terminal panel*. The panel is a tab on the left,
beside Jobs and Inventory, and View → Terminal brings it forward. It runs your login
shell (`$SHELL -l`), so your prompt, aliases and `PATH` are the ones you're used to.
To run something else, set *Shell* (for example `/bin/zsh`). Windows runs a different
default: see [Windows](#windows).

**Restart shell** starts a fresh one if you `exit` or something hangs.

## Windows

The panel runs `%COMSPEC%`, which is `cmd.exe` unless you've changed it, and falls
back to `powershell.exe` if the variable is unset. Windows has no login-shell flag,
so nothing like `-l` is added.

For PowerShell, set *Shell* to `powershell.exe`, or to `pwsh.exe` for PowerShell 7.
The setting is one program, not a command line: a path with spaces
(`C:\Program Files\PowerShell\7\pwsh.exe`) works as typed, without quotes, and
arguments aren't supported there.

It needs Windows 10 version 1809 or later, the first with ConPTY. On anything older
the panel says it couldn't start the shell.

**What's been checked.** CI runs a real `cmd.exe` in a real pseudo-console on a
Windows runner (output, typing, resize, exit code, **Restart shell**'s close),
whenever the Windows build runs. Nobody has used the panel on a Windows machine
yet: the page in QtWebEngine, PowerShell, full-screen programs and the stream view
are untried there. If something's off, [open an issue](https://github.com/IamMrCupp/benchhud/issues).

## Serial consoles

The same panel is also a serial console. **Connect to**, under the toolbar, picks
what it's attached to: your shell, a serial port, or the OWON.

- **A serial port** (anything but the OWON): a raw console, like PuTTY or
  `screen`. Set the speed, what **Enter** sends (CR LF, CR or LF; devices
  disagree) and **Local echo**, for devices that don't echo what you type. A
  bare LF from the device shows as a new line. Unplugging it ends the session
  with a message; **Reconnect** opens it again.
- **The OWON** appears as *OWON SCPI console (queries only)* while telemetry is
  on, and never as a raw port. The meter is already polling it, and a second
  handle on the same port would interleave with those polls, so every line goes
  through the running instrument session instead. It's **queries only**: a line
  must end in `?` with no `;` (SCPI's command separator), so `*IDN?`,
  `MEAS:VOLT?` and `CONFigure:ALL?` work, and `VOLT 24` is refused before it
  reaches the port, **even with instrument control on**. A typed setpoint would
  skip the driver's voltage and current ceilings; setpoints and output go
  through the Meter panel's controls, where the limits apply.

Everything under *On stream* applies to a serial console too: the read-only
view, Pause stream, and nothing typed while paused ever reaching OBS.

## Command-line tools

Some bench tools have no window: `minipro`, `flashrom`, `avrdude`, `sigrok-cli`,
OpenOCD, `picocom`. In the Tools pane they're marked **>_**. Clicking one types
the command at the terminal's prompt **without pressing Enter**, so you add the
arguments (`flashrom -p ch341a_spi -r dump.bin`) and run it yourself.

- **Attached to a serial port?** benchhud asks before switching the panel back to
  your shell, and the answer defaults to No.
- **Terminal panel off**: the tool opens in a system terminal
  window instead: Terminal.app on macOS, a console on Windows, and on Linux the
  first of `x-terminal-emulator`, GNOME Terminal, Konsole, Xfce Terminal, kitty,
  Alacritty or xterm. The window stays open at a shell when the tool exits.
- **There's a GUI for it?** Tools → Find installed tools says so in the tooltip:
  minipro has *minipro GUI* and *qtl866*, `sigrok-cli` has PulseView, `picocom`
  has GtkTerm.

Tools → Find installed tools marks command-line tools for you. For one you add
by hand, tick **Terminal** in Preferences → Tools, or set `terminal = true` in
its `[[tools]]` block.

## On stream

Turn on *Also show it on stream*, then copy the *Stream link* into OBS as a Browser
source. It looks like:

```
http://127.0.0.1:8780/terminal?token=…
```

The stream view renders at the shell's own size and scales to fit the source, so
full-screen programs (`htop`, `vim`) line up. Around 800 × 450 suits a 16:9 scene.

**A shell shows whatever scrolls past:** tokens, keys, customer names in a path.
So the stream view is careful by design:

- **Output only.** Nobody can type through it. Its token only watches, and only the
  in-app view has the per-run token that types.
- **Off by default, and it starts from now.** Turning sharing on never replays
  anything from before.
- **Pause stream**, on the panel's toolbar, hides the terminal from OBS. When you
  resume, the stream picks up from that moment. Nothing typed while paused is ever
  sent, including when OBS reloads the source. **● ON STREAM** shows while it's live.
- **When benchhud isn't running**, the source is blank, never a frozen screen.

The in-app terminal keeps its full scrollback regardless. Pausing only affects the
stream.

**New link** in Preferences makes a fresh stream token, and the old link stops
working. Do that if a link ever leaks, then paste the new one into OBS.

## Who can connect

The terminal runs on the browser-source server (`[browser_sources] port`, default
8780), bound to `127.0.0.1`. The panels there and the terminal are switched
separately: turning on the terminal alone serves only the terminal.

- Every terminal request needs a token.
- Typing needs the in-app token, which is new every launch and is never shown or
  saved.
- Posts must come from the server's own origin, so a web page elsewhere can't drive
  the shell even if it guessed a token.
- The `Host` header must be `127.0.0.1` or `localhost`, which blocks DNS rebinding.

## How it's built

- **The shell** runs in a POSIX pseudo-terminal (`pty.fork`) on macOS and Linux, so
  job control, Ctrl-C and resizing work as in any terminal. On Windows it runs in a
  ConPTY pseudo-console, through [pywinpty](https://github.com/andfoy/pywinpty)
  (MIT), which only installs there. Either way benchhud strips its own
  bundled-Python variables (`PYTHONHOME` and friends) from the shell's environment
  and sets `TERM=xterm-256color`.
- **The screen** is [xterm.js](https://xtermjs.org/) (MIT), the component VS Code
  uses. It's vendored under `resources/web/vendor`, so nothing loads from a CDN.
- **One page, two surfaces:** the panel shows it in QtWebEngine, and OBS loads the
  same page over the local server. Output streams over Server-Sent Events;
  keystrokes and resizes are small `POST`s, sent one at a time so they can't arrive
  out of order.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Panel says *the terminal is off* | Preferences → Terminal → *Show a terminal panel*. |
| Panel says *can't start: port … is in use* | Something else has the browser-source port. Change it in Preferences → Output. |
| Panel says *QtWebEngine didn't load* (Linux) | A system library is missing. Reinstall the `.deb` with `sudo apt install ./benchhud_….deb`, which pulls the dependencies. |
| Blank panel in a self-built Mac app | The bundle is missing the `allow-jit` entitlement. See [PACKAGING.md](PACKAGING.md). |
| Panel says *couldn't start cmd.exe* (Windows) | The pseudo-console didn't open; the message carries the reason. Windows before 10 version 1809 has no ConPTY. |
| OBS source is blank | Sharing is off, the stream is paused, or benchhud isn't running. Check that the link's token matches Preferences. |
