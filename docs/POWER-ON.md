# Power-on signatures

What a board draws in the first seconds after power-on says a lot. A short pegs
at the current limit, a dead PMIC sits at a few tens of mA, and a healthy board
steps up through its rails and settles. benchhud records that curve from the
supply and compares it with a known-good board of the same part number.

## Recording

- **Automatically:** every **Output ON** from the Meter panel records the next
  10 seconds.
- **By hand:** **Record** under *Power-on signature* starts one now, for a board
  you power yourself or one already running.

It needs instrument telemetry on, since the curve comes from the supply's
readings. At the OWON's 0.2 s poll that's enough to see rails come up, not
inrush.

With a job selected, the recording is saved on the job:

- a timeline note, e.g. `Power-on signature: peak 0.900 A, settled 0.050 A over
  10 s, LOW from 2.0 s: 0.050 A where a good board draws 0.250 A.`
- a chart (`power-on-<date>.png`, the note's attachment), with the known-good
  curve dashed behind it
- the raw samples (`power-on-<date>.csv`) beside it

## Comparing

The curve is compared with the median of the known-good curves for the job's
board number, every half second from 1 s on. The first second is skipped
because inrush differs run to run.

| Result | Means |
|---|---|
| **matches** | Within 25% of the good boards, or 20 mA, all the way |
| **LOW from *n* s** | Draws less than a good board from there on: a rail not coming up |
| **HIGH from *n* s** | Draws more: a short, or something stuck on |

It takes two steps in a row outside the band to flag, so one noisy poll doesn't.

While recording, the Meter's chart draws the known-good curve dashed, on the
current axis (right-hand side, in the current colour), starting from the moment
the output came on.

## Known-good curves

After powering a good board, or once a repair is proven, press **Known-good**.
The last recording becomes a reference for the job's board number. It needs a
board number to be one.
