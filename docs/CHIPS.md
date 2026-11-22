# Chip reference

There's a part under the microscope with `X1=6A` printed on it and nothing else.
What is it?

benchhud answers that from data you keep on the bench box: type the mark, or a
part number, and get the chip — who makes it, its package, what it does, and the
datasheet if you hold it. It works offline, and it can read from more than one
place at once, so a private notebook of your own finds sits on top of a shared
set without either one swallowing the other.

benchhud is the reader. It doesn't hold chip data, doesn't run git, and doesn't
download anything — not the data, not a datasheet. The folders are yours, the
way `~/boards` is.

---

## Where the data comes from

A **source** is a folder in one of two shapes:

```
~/chips-private/                  one file per chip
  chips/
    example-semi/
      EX1234QFN.toml

~/chip-build/                     a built index: every chip in one file
  index.json
```

A checkout that has both — chip files, and an `out/index.json` built from them —
is read from the index while the index is current. If anything under `chips/` or
`vocab/` is newer than the index, the chip files are read instead, so a pull
without a rebuild can't leave you on last week's data.

benchhud doesn't come with chip data, and there's no public set to point it at.
A source is whatever you write down yourself — and that's the part worth having:
the marks you've actually read off parts.

A chip file is TOML. This is a whole one — "Example Semi" is made up:

```toml
schema = 1
mpn = "EX1234QFN"
manufacturer = "Example Semi"
package = "QFN-20 (3x3)"
functions = ["buck-controller"]
description = "Single-phase buck controller for a standby rail"

[[markings]]
code = "X1="                    # the part of the mark that never changes
examples = ["X1=6A", "X1=7C"]   # whole marks, as read off real parts

[[documents]]
kind = "datasheet"
filename = "EX1234.pdf"
sha256 = ["0123…cdef"]          # optional; see "Datasheets" below
```

The first three lines are the smallest valid file. A file benchhud can't make
sense of is skipped with a line in the log, and the rest still load.

---

## Setting it up

**Preferences → Chips** is the list of sources, in order: name, folder, a tick
to switch one off without deleting it, and an **On air** tick that decides
whether the stream card may use it. Add, Remove, Move up, Move down, and a
folder picker. The last column says what each folder actually came to —
`412 chips, from the built index`, `12 chips, from the chip files — 1 note`,
`Nothing loaded: …` with the reason — read by the same code the app uses, so
it's what OK will give you.

The same thing in the config file:

```toml
[chips]
datasheet_dir = "~/datasheets"

[[chips.sources]]          # order is precedence
name = "local"
kind = "dir"               # the only kind there is
path = "~/chips-private"
on_air = false             # yours to look up; never on the stream card

[[chips.sources]]
name = "community"
kind = "dir"
path = "~/chip-build"
enabled = true             # false leaves it out
```

With no sources there's no chip data and nothing else in benchhud changes.

The folders are read once, at start, on a background thread. OK in Preferences
reads them again. Nothing watches a folder — after you add a chip or pull a
checkout, open Preferences and press OK, or restart.

### When two sources know the same chip

A chip is its full part number, case ignored. When more than one source has it,
they're put together:

- **A single value** — maker, package, description — comes from the first source
  in the list that has one. So a private overlay goes **above** shared data.
- **If a later source says something different**, it isn't thrown away. The chip
  is marked as having a conflict, and Find chip shows every source's value.
  Different case or stray spaces don't count as different.
- **Lists** — marks, documents, substitutes, boards — are joined, repeats dropped.

### Keeping a source off the stream

The Chip panel is stream output. Find chip and the inventory row are yours. A
source can be in the second and not the first: put `on_air = false` on it, or
untick **On air** for it in Preferences → Chips. That's what a private overlay
is for — your own words about a part, there when you look it up, and not read
out to the room.

Off air, a source is left out of the card completely. Nothing is merged and
then trimmed: the card looks the chip up in the on-air sources and nowhere
else. So:

- **A chip only an off-air source has isn't on the card.** It reads "No chip
  with that part number", the same as a typo.
- **A chip both have shows what the on-air sources say.** The overlay's
  description, package and marks stay in Find chip.
- **A disagreement with an off-air source isn't mentioned.** The card only
  flags sources it can see.
- **The card never says anything is being held back.**

Find chip and the inventory row still use every source, off air or not. So
they can show more than the card will — that's the point.

A source is on air unless you say otherwise. Write `on_air` as anything but
`true` or `false` and the source is kept off air, with a line in the log: a
typo shouldn't be what puts an overlay on stream.

With every source off air the card finds no chip at all. Preferences → Chips
says so under the table. The card doesn't.

The switch takes hold on OK, a chip already on the card included — it's looked
up again in what's on air now.

---

## Datasheets

A chip lists its documents by **fingerprint**: a filename, and optionally the
SHA-256 of the file. Never the file itself. The files live in a folder of yours —
`datasheet_dir`, or the Datasheets field in Preferences → Chips — searched
recursively.

When you open a chip's datasheet, benchhud looks for it two ways:

- **By hash.** A file whose SHA-256 is one the chip lists *is* that document,
  whatever it's called and wherever it's filed.
- **By name**, failing that. A file with the same name is offered — and marked as
  a name match, because it might be another revision.

You're told which you got. The inventory row says it before you open anything —
`held, an exact copy`, or `held, matched by name only; it may be another
revision`. Find chip says it as the file opens.

benchhud shows the chip's name for a document, never the name or folder of the
file on your disk. A file found by its hash can be called anything, and some of
these screens can be on stream.

---

## Find chip…

**Chips → Find chip…** is the lookup. One box, tried both ways: what you type is
looked up as a mark and as a part number, and the answers come back as one list.

- A **mark** is found from its first character, and narrows as you type. The
  whole mark is fine — `X1=6A` finds the chip whose code is `X1=`.
- A **part number** has to be typed in full.
- **It's a list, never one answer.** The same three characters turn up on
  different chips in different packages, so every candidate is shown with its
  package and functions. If there are several, none is picked for you.

It's built for one hand: arrows move through the candidates, Enter opens the
picked one's datasheet, and typing always goes to the box. There's no keyboard
shortcut to open it — on Linux every Ctrl chord is one the Terminal panel's shell
might want.

The window stays where you put it, beside the HUD. Come back to it and the text
and the pick are as you left them.

Pick a candidate and the lower pane has the rest: marks with examples, documents,
substitutes, the boards it's been seen on. **With more than one source**, it also
says which source each value came from, and where sources disagree it lists
every source's value with the one in use marked. This is an operator window, so
it shows all of that — source names and the values that lost included.

**Show on stream card** puts the picked chip on the Chip panel, where viewers can
see it. It's a mouse click — not Enter, and never something that happens because
you picked a row. Only the part number goes: the card looks the chip up for
itself, so nothing else in this window goes with it.

It looks in the on-air sources only. So the card can show less than this window
does — and for a chip that's only in a source you keep off air, it shows "No
chip with that part number". This window says the part number was sent; it
doesn't know what the card made of it.

---

## On an inventory row

With a chip source set up, selecting a part in the **Inventory** panel that has
an MPN shows what the chip reference knows about it under the Use on job row —
maker, package, functions, description, marks — with **Open datasheet**.

When the reference knows the part, **Show on stream card** is there too, and
puts that chip on the Chip panel. Same rules as in Find chip: a mouse click — no
key reaches it — and only the part number goes. Nothing about the part, the shelf
or the job. And the same limit: the card shows what the on-air sources say,
which can be less than the row does.

A part with no MPN shows nothing. An MPN the reference doesn't know gets one line
saying so. With no chip sources, the panel is exactly what it was.

---

## The chip panel — what goes on stream

The **Chip** panel is a card for one chip, and it's stream output: a viewer can
read it. A chip gets there three ways — type a part number into the panel, or
press **Show on stream card** in Find chip or on an inventory row. That brings
the Chip tab forward, opening it if you'd closed it, and replaces whatever chip
was on the card. Whichever way it arrives, the card shows:

- the part number, the maker and family
- the package
- what it does
- the marking codes
- the description

and that's all. It leaves out every note, the boards a chip has been seen on,
documents, substitutes, whole-mark examples, credits, and which source said what.
When sources disagree about something it shows, it says which thing — "Sources
disagree on package" — without naming the sources or the other values.

**If you keep a private overlay, decide whether it's on air.** The card reads
the on-air sources, merged, and an overlay is usually first in line. Left on
air, its description, functions and marking codes for a chip are what viewers
read. Notes never are — but a description is a description, and **Show on
stream card** is one click.

Take it off air and none of it reaches the card: see
[Keeping a source off the stream](#keeping-a-source-off-the-stream). A source
you add starts **on air**, so that's a tick to remove, not one to remember to
add.

Find chip and the inventory row's datasheet line are for you; the card is for
the room.

---

## Looking a mark up on the web

When your own data doesn't have the chip, Find chip has a row of buttons that
hand the mark to a marking site in **your browser**. benchhud itself never makes
a network request — it builds an address and passes it to the browser, and only
when you click.

Each button is named for where it goes, and each tells that site something:

| Button | What the site is told |
|---|---|
| `smd.yooneed.one` | The whole mark you typed. |
| `www.s-manuals.com` | The mark's first two characters. |
| `www.ti.com` | The whole mark. It only knows TI parts. |
| **Maker's page** | Nothing typed — it opens the address a document lists for itself, and only if that's a plain `https://` one. |

Only a mouse click does it. Not typing, not Enter — those buttons can't take the
keyboard at all.

### The recon lock

While a recon engagement is active, the web buttons are off. A marking search
names a part to a stranger, and on an engagement that part is on someone's
target.

**An engagement stays active until you end it.** Importing one turns the web
lookups off, and they stay off — through the customer repair you do after lunch
too — until you press **End engagement…** in the Engagement panel and confirm.
That clears the engagement from the recon panels and turns the lookups back on;
Find chip says where to go while they're off. Ending is local: nothing changes
in bench-recon, and importing the engagement again picks it back up. Quitting
benchhud ends it as well. Looking chips up in your own data is unaffected
either way.

---

## What it never does

- **No network.** Nothing here fetches anything. The web buttons hand an address
  to your browser.
- **No git.** A checkout is a folder. Keeping it current is yours.
- **No downloads.** A document's address is something you can open, not
  something benchhud collects.
- **No writing.** Sources and the datasheet folder are read-only to benchhud.
- **Nothing about a job or a customer.** A chip is a part number and what's
  printed on the part. None of these screens are given a job, a ticket or an
  engagement to show.

---

## When it doesn't work

- **"No chip data is configured"** in Find chip — add a source in Preferences →
  Chips.
- **A source says `Nothing loaded`** — the rest of the line is why: the folder
  isn't there, it has no `index.json` and no `chips/`, or the index is in a
  format newer than this benchhud reads.
- **`— 1 note` after a count** — hover it. A file was skipped, or an index was
  passed over for being older than the chip files.
- **The Chip panel says there's no chip with that part number, and Find chip
  finds it** — the chip is only in a source that's off air. Preferences → Chips,
  the **On air** column.
- **A chip you just added isn't found** — nothing watches the folders. Press OK
  in Preferences; an open Find chip looks again by itself.
- **Open datasheet is off** — on the inventory row: the chip lists no datasheet,
  the file isn't in the folder, or no datasheet folder is set, and the line above
  the button says which. In Find chip it's off only when no datasheet folder is
  set or the chip lists no documents; a file that isn't held is said when you ask
  for it.
- **The web buttons are off** — nothing is typed, what's typed isn't a plain mark
  a site can be asked about, or a recon engagement was imported this run. The
  line above them says which.

The log has a line for everything skipped: `chips:` for sources, `datasheets:`
for the folder.
