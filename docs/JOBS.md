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

## Where the data lives

Beside your live config file:

```
<config dir>/jobs.db                     # the SQLite store
<config dir>/job_attachments/<job_id>/   # capture images
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
