# OBS browser sources

The virtual camera is one picture. OBS's camera extension has a single sink, so
everything benchhud sends that way arrives as one source, and the meter can't
sit in a different corner of a different scene from the scope.

Browser sources fix that for the data panels. benchhud serves each one as its own
web page on your machine, and OBS adds each page as a Browser source you can
place, scale and crop on its own. Same on macOS, Linux and Windows, with nothing
to install.

## Turning it on

Preferences → Output → **Serve panels to OBS as Browser sources**. Or in the
config:

```toml
[browser_sources]
enabled = true
port = 8780
```

It applies on OK, no relaunch. Open `http://127.0.0.1:8780/` for the list of
panel URLs.

## The panels

| URL | What it shows |
|---|---|
| `http://127.0.0.1:8780/panel/meter` | The meter: DMM line plus the V / A / W / max-current cells, in the dock's LCD style and colours. |
| `http://127.0.0.1:8780/panel/graph` | The live V / A / W trend. Each line gets its own vertical range, as the on-stream graph does. History length follows *Graph history* in Preferences. |
| `http://127.0.0.1:8780/panel/job` | The selected job: device, board number, ticket ref, status. Empty when no job is selected. |

In OBS: **+** → **Browser** → paste a URL. Width 400 × height 200 fits the meter
and the graph at 1×. Every page has a transparent background, so there's no
chroma key to set up — it layers straight over the scope.

Video stays on the virtual camera. Separate scope and thermal sources are a
different problem (Syphon on macOS, Spout on Windows) and not part of this.

## What goes on air

**The job card never carries the customer.** It sends a fixed list of fields
(`id`, `device`, `board_id`, `status`, `external_ref`) rather than hiding the
ones that shouldn't go, so a field added to jobs later stays off stream until
someone decides otherwise. The serial number and the symptoms stay off too: a
serial identifies an owner, and symptoms are free text typed from what the
customer said.

## When benchhud isn't running

The pages keep trying to reconnect and pick the feed back up on their own when
benchhud starts. In between they dim and show "—" rather than the last reading,
so a stale number can't pass for a live one on stream.

## Who can connect

The server listens on `127.0.0.1` only, so nothing else on the network can reach
it. It also refuses any request whose `Host` isn't `127.0.0.1` or `localhost`,
which keeps out a web page in a local browser trying to reach the port under its
own name (DNS rebinding). OBS on the same machine is the audience; OBS on a
second streaming PC isn't supported.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Status bar: *browser sources off — port 8780 is in use* | Something else has the port. Pick another in Preferences and update the OBS URLs. |
| Page is blank in OBS | Check the URL starts `http://127.0.0.1:` and that the option is on. `http://127.0.0.1:8780/` in a normal browser should list the panels. |
| Page shows "—" everywhere | benchhud isn't running, or has no readings yet. The meter reads "—" until the PSU answers. |
| Job card is empty | No job is selected in the Jobs panel. |
