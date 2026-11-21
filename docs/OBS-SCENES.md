# OBS scenes from the Tools dock

Some bench tools have windows of their own — PulseView, OpenBoardView, a chip
programmer. On stream each one gets an OBS scene. benchhud can pick which scene
is live, so showing a tool doesn't mean a trip to OBS and back.

OBS still owns the pictures: the window captures and browser sources are built
in OBS. benchhud only chooses the scene.

## Turn it on

1. In OBS: **Tools → WebSocket Server Settings → Enable WebSocket server**.
2. In benchhud: **Preferences → Tools → Connect to OBS**.

The Tools dock grows an **On stream** row showing the live scene. Pick another
to switch.

benchhud reads the WebSocket password from OBS's own settings file on the same
machine, so there's nothing to copy. For an OBS on another machine set `host`,
`port` and `password` under `[obs]` in the config file.

## A scene per tool

In **Preferences → Tools**, fill in **OBS scene** for a tool — the exact name
of the scene its window is in.

Then tick **Show a tool's scene when it starts, and go back when it exits**.
That second tick is separate on purpose: it changes what's on air without
anyone touching OBS, so it's yours to decide.

With it on:

- Starting the tool shows its scene.
- When the tool exits, the scene goes back to whatever was showing before.
- If the scene was changed by hand in the meantime, it's left where it is.
- A tool with no scene never changes anything.
- A command-line tool (one that opens in the Terminal panel) shows its scene
  but nothing brings it back: it's typed at a prompt, so there's no window
  closing to go by.

## In the config file

```toml
[obs]
enabled = true
auto_switch = true

[[tools]]
name = "PulseView"
command = "pulseview"
scene = "Logic Analyzer"
```

## Limits

- If OBS isn't running, nothing happens and nothing complains. benchhud tries
  again every few seconds and picks up when OBS appears.
- On Linux under Wayland, OBS captures a window through the desktop's
  screen-sharing prompt, which someone has to answer at the machine once per
  source.
