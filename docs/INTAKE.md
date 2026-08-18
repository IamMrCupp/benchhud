# Ticket intake

benchhud is the **bench-side client** of a shop ticketing system, not the
ticketing system itself. Intake — customer, device, symptoms, accessories, the
front-counter paperwork — belongs in its own service. benchhud pulls a work
queue from it and links each local job to a ticket by reference.

That split is deliberate. The bench doesn't need to know how a board got here,
only that it's here and what it's supposed to do wrong.

**This is optional.** Leave `base_url` empty and benchhud runs entirely
standalone — the local job store is the bench log either way. See
[JOBS.md](JOBS.md).

---

## Configure it

```toml
[intake]
base_url = "http://intake.repair-bench:8000"
token    = "…"
```

`token` is intake's `INTAKE_API_TOKEN`, sent as a bearer token. Both fields are
editable in **Preferences → Tickets**; the token is kept out of the logs.

With `base_url` empty, benchhud constructs a null source — the **Import…**
button finds an empty queue and nothing else changes.

---

## The API contract

Read-only, LAN-only, bearer-authed. Two endpoints:

```
GET /api/tickets?status=open   ->  [Ticket, ...]
GET /api/tickets/{ref}         ->  Ticket | 404
```

A ticket carries seven fields:

| Field | Notes |
|---|---|
| `ref` | The stable external identifier. This is what benchhud stores on the job. |
| `device` | The board or unit. |
| `symptoms` | What it does wrong, as reported. |
| `customer` | **PII — never composited.** |
| `status` | Intake's status string. |
| `url` | Deep link back to the ticket, when there is one. |
| `board_id` | Board part number (e.g. `820-00239`). Not PII. Optional. |

Parsing is forward-compatible in both directions: **extra keys are ignored**, and
absent optional keys fall back to the defaults. An intake that only sends the
first six keys parses fine — `board_id` just comes back `None`.

> **On `board_id`:** benchhud resolves it against the local board library the
> moment a ticket lands — see [BOARDS.md](BOARDS.md). If your intake doesn't send
> it yet, the field comes back `None`, the board row stays quiet, and a later
> re-sync backfills it once intake starts including it. Nothing breaks either way.

HTTP goes through the stdlib `urllib` — no new dependency, matching the
project's dep-conscious habit elsewhere.

---

## Importing a ticket

**Import…** in the Jobs dock pulls the open queue and opens a picker showing
each ticket's ref, device, and board id. Pick one and it lands on the bench as a
local job, with `external_ref` set to the ticket's `ref`.

**Import is idempotent.** Importing the same ticket twice returns the existing
job rather than duplicating it — matched on `external_ref` — so re-syncing the
queue whenever you feel like it is safe.

Import copies `device`, `customer`, `symptoms`, and `board_id` onto the job.
The one exception to "snapshot" is `board_id`: re-importing a ticket **backfills**
it onto the existing job if intake has started sending it since. Otherwise it's a
snapshot,
not a live link: editing the job on the bench doesn't write back to intake, and
benchhud never PUTs or POSTs anything. The API is read-only by design.

---

## When the LAN is down

The HTTP source **never raises into the HUD**. An unreachable bench box, a bad
token, a malformed payload — each degrades to an empty queue (or `None` for a
single lookup) plus a log line. A flaky network can't take the bench down
mid-repair.

That means an empty ticket list is ambiguous by construction: it's either
genuinely no open tickets or intake being unreachable. Check the log if the
difference matters. Requests time out after 5 seconds.

| Symptom | Cause / fix |
|---|---|
| Import… shows an empty list | No open tickets, or intake unreachable — check the log for `intake unreachable`. |
| Log shows `intake … -> HTTP 401` | Bad or missing token. Compare against intake's `INTAKE_API_TOKEN`. |
| Log shows `intake … -> HTTP 5xx` | Intake service is up but erroring — check it on the bench box. |
| Ticket imports with no board id | Expected today — intake isn't sending `board_id` yet (see above). |

---

## Privacy

The composite goes to a virtual camera, and a stream is a bad place to leak a
customer name. So: **`customer` never renders into a composited surface.** The
ticket picker and the Jobs dock show it for identification — neither is
composited — and it stops there.

`board_id` is a part number, not personal data. It's safe anywhere, including
on-stream.

---

## Other backends

Everything goes through the `TicketSource` protocol — `name`,
`list_open_tickets()`, `get_ticket(ref)`. The HTTP intake client is just the
first implementation. `NullTicketSource` (standalone) and `InMemoryTicketSource`
(tests, demos, offline fallback) ship alongside it, and another shop system is a
new class satisfying three methods, with no consumer changes.
