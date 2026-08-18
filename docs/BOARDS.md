# Board documentation

"Do I have this board?" is the first question when a job lands. benchhud keeps a
**local library** of board-view files on the repair box, keyed by the board's
part number, and answers it — from a job, from a quick search, or across the
whole open ticket queue at once.

benchhud doesn't render board views. It locates the file and hands it to a real
viewer. That's the whole design: the library is a directory you already keep,
and benchhud makes it reachable from where you're working.

---

## The library

A directory of board files named after the board id:

```
~/boards/
  820-00239.obv
  820-00239.brd
  apple/820-01700.bdv
```

Point `boards.library_dir` at it. Scanning is **recursive**, so file into
sub-folders by vendor or model if you like — the board id is the filename stem
either way, matched case-insensitively. Several formats for one board is fine;
they're all resolved together.

```toml
[boards]
library_dir = "~/boards"
extensions  = ["obv", "brd", "bdv"]   # board-view formats
```

Leave `library_dir` empty and the whole feature is inert — no errors, the board
row just stays quiet. All of this is also in **Preferences → Boards**.

`board_id` is the board's part number, not personal data. It's safe to show
anywhere, on-stream included, which is why it's the key for all of this.

---

## From a job

A job carries a `board_id` — set it by hand, or it arrives with an imported
ticket (and gets backfilled on re-sync if intake didn't send it the first time).
The Jobs dock's **board row** resolves it against the library live:

- **Found** — the row names the file(s) and **Open board** hands the file to
  your viewer.
- **Not found** — the row says so. That's your cue to go source it.

`board_id` lives on the job itself, as a column, so this resolution works
**offline** — no round-trip to intake to find your own board.

---

## Find board…

**Boards → Find board…** is the quick search over the library, independent of
any job. Type part of an id and it filters live; a blank query lists everything.
Pick a result and open it.

This is "do I already have this?" for a board that just walked in, before there's
a job for it.

---

## Boards to source…

**Boards → Boards to source…** turns "do I have it?" into a work queue: it
takes the open ticket queue from intake, pulls each ticket's `board_id`, and
lists the ones with **no file in the library** — grouped by board, so a part
number that three tickets need shows once with all three refs. It mirrors a
parts "to order" list.

The worklist carries only `ref`, `device` and `board_id` — **never `customer`** —
so it's safe to have up on-stream while you go find files.

Needs intake configured (see [INTAKE.md](INTAKE.md)); with no ticket source it's
empty by construction.

---

## Opening the board

**Open board** resolves your viewer from the **Tools launcher** entries you've
already configured — there's no second place to point at the same binary. The
file is appended as an argument. With no matching viewer configured, it falls
back to the OS file handler for that extension.

Which viewer, and how schematics come into it, is configurable — see the
`viewer` and `schematic_extensions` keys in `config.example.toml`.

---

## What it isn't

- **Not a viewer.** benchhud never draws a board. OpenBoardView, FlexBV, or
  whatever handles the format does that.
- **Not a downloader.** The worklist tells you what to go get; finding and
  fetching board files is on you.
- **Not a backup.** The library is a plain directory. rsync it to the NAS on
  whatever schedule you already run — that's not benchhud's job.
