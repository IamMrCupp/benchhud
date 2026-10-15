# Flash

The **Flash** panel reads, backs up and writes SPI flash chips (BIOS, EC and
firmware chips) with [flashrom](https://flashrom.org). Every read and write is
filed with the selected repair job.

It's the same job flashrom does on the command line, with the risky parts
handled:

- **A backup is two reads that match.** A clip that slipped reads garbage
  without complaint. So the panel reads twice and compares, and only a matching
  pair is saved. If the reads differ, it says to reseat the clip and keeps both
  reads for a look.
- **Write and Erase ask first,** defaulting to No. They name the chip, and they
  warn when there's no backup of it from this session.
- **An image of the wrong size is refused** before anything runs. That's either
  the wrong image or the wrong chip.
- **flashrom verifies every write itself.** Its verdict is what the panel
  reports.

## Using it

1. Clip the chip, mind pin 1, and plug in the programmer.
2. Pick the **Programmer**. The list has the common ones (CH341A, Raspberry Pi
   SPI, FT2232H, serprog, Dediprog); type any other `-p` value flashrom takes.
   The last one is remembered.
3. **Detect.** The panel shows the chip flashrom found. If several definitions
   match (common with Macronix parts), pick the one printed on the chip.
4. **Read backup.** With a job selected, the dump goes into the job's
   attachments as `<chip>-<date>.bin`, with a `.sha256` beside it, and a note on
   the timeline. Without a job, it asks where to save.
5. **Write…** or **Erase…** when you mean it. **Stop** ends flashrom. During a
   write or erase it asks first, because stopping leaves the chip half-written.

flashrom's own output scrolls in the panel, so nothing is hidden.

## On the job's timeline

| Event | Note |
|---|---|
| Backup | `Flash backup: the Winbond W25Q64FV (8192 kB) via ch341a_spi, two reads match. sha256 3f2a…`, with the dump attached |
| Reads differ | `Flash read: … the two reads differ. Not a backup.` |
| Write | `Flash write: bios.bin (sha256 …) to the … via …, verified.` |
| Erase | `Flash erase: the … via ….` |
| Failure | `Flash write FAILED on the … via …: …` |

The job report lists the dump by file name.

## Installing flashrom

benchhud runs flashrom as a separate program, never linked, so any recent
version works.

- **Ubuntu:** `sudo apt install flashrom`. A CH341A needs USB access as your
  user: the `plugdev` group from [BENCH-SETUP.md](BENCH-SETUP.md) and a udev
  rule (flashrom's package ships one).
- **macOS:** `brew install flashrom`.
- **Windows:** flashrom has Windows builds, and USB programmers need the WinUSB
  driver (installed with Zadig). The panel runs there, but it hasn't been tried
  on a Windows bench yet.

If it isn't on `PATH`, set its location under `[flash] command` in the config.

## The T48 panel (minipro)

For chips on an XGecu **T48** (or TL866II+) in the ZIF socket or on a clip:
EEPROMs like the 24C02, SPI flash, parallel flash. It drives
[minipro](https://gitlab.com/DavidGriffith/minipro) the same way the Flash panel
drives flashrom, with the same rules: a backup is two matching reads, writes and
erases ask first, a wrong-size image is refused, and everything goes on the job.

The difference: minipro can't detect a chip, so you tell it.

1. Type part of the name (`W25Q64`, `24C02`) and **Find**. Pick the exact part
   *and package* (`W25Q64BV@SOIC8`); the panel never guesses one. It remembers
   the last chip.
2. **Check ID** reads the chip's ID and says one of three things:
   - it matches;
   - no chip answered (`0x0000`): seating, pin 1 at the lever end, or the wrong
     package;
   - it's a different part, with both IDs, so you can pick the right one.
3. **Read backup**, **Write…**, **Erase…** as on the Flash panel.

**Keep the T48's firmware current.** On the bench, minipro warns *Firmware is
out of date* (00.1.03, it expects 01.1.39) and *T48 support is not yet
complete*. Old firmware is a common cause of failed or flaky reads. XGecu's
Xgpro (Windows) updates it.

