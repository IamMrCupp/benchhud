# Repair jobs

A board-repair bench is job-shaped: a board arrives with symptoms, you measure
and replace things, it ends up fixed or scrapped. benchhud keeps that record on
the bench — a SQLite file beside your config, no server, no account, works
offline.

The part a standalone ticket app can't do: benchhud already holds the cameras and
the instruments, so one button appends *what you were looking at and measuring* —
scope frame, thermal frame, live V/A/W — to the job as an image plus a summary
note. **The repair log writes itself while you work.**

Jobs are local-first and standalone. Pulling work from a shop ticket system is
optional and layered on top — see [INTAKE.md](INTAKE.md).

---

## The job record

| Field | What it holds |
|---|---|
| `device` | The board or unit — the one required field. |
| `serial` | Serial number, when the board has a legible one. |
| `customer` | Who it belongs to. **PII — never composited to the stream.** |
| `symptoms` | What it does wrong, as reported. |
| `status` | One of the five below. |
| `external_ref` | The ticket reference, when the job came from intake. |
| `board_id` | The board's part number (e.g. `820-00239`). Not PII. Resolves against the local board library — see [BOARDS.md](BOARDS.md). |

Statuses are `open`, `in_progress`, `waiting` (on parts or on the customer),
`done`, and `scrapped`. Reports render them as human labels — `scrapped` prints
as "Not repairable."

Every job carries a **note timeline**, oldest first. A note is text plus an
optional image attachment, which is what makes the capture action work.

---

## The Jobs panel

The dock has three parts, top to bottom:

**New / import row** — type a device name and hit **New job**, or hit
**Import…** to pull a ticket from intake (see [INTAKE.md](INTAKE.md)). The list
below shows jobs newest first.

**Detail form** — device, serial, customer, symptoms, status, board id. **Save**
writes changes and bumps the timestamp. **Export report…** writes a
customer-facing Markdown report. Below the form, the **board row** resolves the
job's `board_id` against your local library and offers **Open board** when it's
found — see [BOARDS.md](BOARDS.md).

**Notes timeline** — the work log. Type and hit **Add note** (or Enter) for a
text entry, or hit **Capture bench** to attach the live bench state.

The panel also shows a **related-jobs** line: prior jobs for the same board. A
non-empty serial is the strong signal — same physical board, so it wins. With no
serial it falls back to matching the device model. That's the "I've seen this
board before" check, and it's the reason logging serials pays off later.

---

## Capture bench

**Capture bench** composes whatever the bench currently has — scope frame,
thermal frame colorized with the current palette — side by side, matched in
height, and writes it as a PNG attached to a new note. The note text summarizes
the readings:

```
12.05 V · 0.42 A · 5.1 W · DMM[VDC] 3.31 · max 84.2°C
```

Any part may be missing. No thermal cam means a scope-only image; no instrument
means the temperature alone. With nothing at all attached it still writes a bare
`capture` note.

Images land in `job_attachments/<job_id>/` beside the database, stamped with the
capture time.

---

## Automatic fault logging

Powering a shorted board spikes the current. Rather than making you reach for a
button mid-measurement, benchhud can log that itself:

```toml
[faults]
auto_log = true
current_threshold_a = 1.0
```

The detector fires on the **rising edge** — the moment current crosses the
threshold — then stays quiet until it falls back below
`threshold - hysteresis` (0.05 A by default). So a board that's powered, shorts,
and gets powered off yields exactly one note, not one per poll while the current
sits high, and noise around the threshold doesn't chatter.

The note reads:

```
⚡ Over-current fault: 1.42 A (threshold 1.00 A)
```

Both settings are in **Preferences → Faults** too. Off by default — tune the
threshold to your bench before switching it on. A threshold of zero or less
disables detection outright.

---

## Reports

**Export report…** renders the job and its timeline as Markdown: a details
table, the reported symptoms, and the work log with each note timestamped and
any capture referenced by filename. Markdown because it's readable as plain
text, needs no dependency, and any viewer (or a browser's print-to-PDF) turns it
into something you can hand over.

Report generation is a pure function of the job, its notes, and a passed-in
timestamp — nothing in it reads the clock, so it's deterministic under test.

---

## From a terminal

Selecting a job used to be a click in the list and nothing else. Fine at the
bench — no use from the couch with the stream already live and the job card
sitting empty. So the running app takes three commands:

```
benchhud job list
benchhud job new MacBook Pro A1708
benchhud job select 12
```

- **`list`** — the jobs, newest first. The `*` marks the selected one.
- **`new`** — creates the job and selects it, same as the **New job** button. No
  quotes needed around the device name.
- **`select`** — same as clicking the row. The Jobs dock moves, and so does the
  job card on stream.

Add `--json` to any of them for a script or a Stream Deck button. Over SSH it's
the same thing: `ssh bench benchhud job select 12`.

**It talks to the app that's already running.** The commands go to benchhud's
local server — the one behind the [browser sources](BROWSER-SOURCES.md) — so that
server has to be on (Preferences → Output → **Serve panels to OBS as Browser
sources**, or the terminal panel). If benchhud isn't running, the command says so
and exits 1. It never opens `jobs.db` itself — a row written behind the app's
back wouldn't show in the list anyway.

**Who's allowed.** The server listens on `127.0.0.1` only, and every command
needs a token. benchhud makes a new one each time the server starts and writes it,
with the port, to `control.json` beside the config — owner-only (`0600`), gone
again when the app closes. The CLI reads that file, so "can read your config
dir" is the whole permission model: you, on that machine. The token goes in a
request header, never the URL, and is never logged or printed. A web page in a
local browser can't use it — it can't read the file, and a request carrying
another site's `Origin` or a `Host` that isn't loopback is refused before the
token is even looked at.

**What comes back** is what the job card gets: `id`, `device`, `board_id`,
`status`, `external_ref`, plus which job is selected. Never `customer`, `serial`
or `symptoms` — the terminal you typed it in might be on stream too.

Jobs only. It doesn't drive outputs, scenes or instruments.

---

## Where the data lives

Beside your live config file:

```
<config dir>/jobs.db                     # the SQLite store
<config dir>/job_attachments/<job_id>/   # capture images
<config dir>/control.json                # port + token for `benchhud job`, while the app runs
```

Schema migrations are **additive only** — new columns get added to an existing
database on open, so an older `jobs.db` keeps working. Deleting a job cascades to
its notes; the attachment files are left on disk.

There's no built-in backup. The database is a single file — rsync it wherever
you keep bench backups.

---

## Privacy

benchhud composites panels to a virtual camera for streaming, so the rule is
simple: **`customer` is PII and never reaches the composite.** It shows in the
Jobs dock and the ticket picker — neither of which is a composited surface — and
it appears in exported reports, which are for the customer anyway.

`device`, `serial`, `symptoms`, and `board_id` carry no personal data and are
safe anywhere.
