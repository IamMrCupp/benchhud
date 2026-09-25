# Terminal

A real terminal inside benchhud: your login shell, in a panel beside the scope and
the meter. Flash a board, tail a log or poke a serial tool without leaving the HUD.
The same terminal can also go on stream as an OBS Browser source, output only.

macOS and Linux. Windows needs a different backend (ConPTY), and it isn't built yet.

## Turning it on

Preferences → **Terminal** → *Show a terminal panel*. The panel is a tab on the left,
beside Jobs and Inventory, and View → Terminal brings it forward. It runs your login
shell (`$SHELL -l`), so your prompt, aliases and `PATH` are the ones you're used to.
To run something else, set *Shell* (for example `/bin/zsh`).

**Restart shell** starts a fresh one if you `exit` or something hangs.

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

- **The shell** runs in a POSIX pseudo-terminal (`pty.fork`), so job control, Ctrl-C
  and resizing work as in any terminal. benchhud strips its own bundled-Python
  variables (`PYTHONHOME` and friends) from the shell's environment and sets
  `TERM=xterm-256color`.
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
| OBS source is blank | Sharing is off, the stream is paused, or benchhud isn't running. Check that the link's token matches Preferences. |
