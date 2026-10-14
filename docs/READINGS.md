# Readings

The **Readings** panel keeps test-point measurements and compares them with a
known-good board as you probe. Board-level diagnosis is mostly this: diode mode
to ground on each rail, against the same point on a good board. A rail at
0.002 V where a good board reads 0.412 V is the short.

## Recording

1. Select the job. For a comparison, it needs a board number (`820-00239`),
   from an imported ticket or the job's board.
2. Put the meter in diode mode (or Ω, or V DC) and probe.
3. Type the test point (`PP3V3_S5`, `C7720`, `TP12`) and press **Enter**. The live
   DMM reading is saved on the job.

No meter connected, or a handheld one? Untick **From the meter**, pick the mode
and type the value. `OL` means open.

Points are case-insensitive, and the field completes names this board has used
before.

## Comparing

Each row shows this board's reading, the known-good value, and a result:

| Result | Means |
|---|---|
| **OK** | Within 10% of the known-good value, or within the mode's floor (diode 0.02 V, Ω 2 Ω, V 0.05 V), so near-zero readings don't flap |
| **LOW** / **HIGH** | Outside that. LOW in diode mode is the classic short |
| **OPEN** | This board reads OL where the good one conducts |
| **TODO** | The good board has this point and this job hasn't measured it yet |

The known-good value is the median of every known-good reading at that point
for the board number, so one odd board doesn't drag it. Re-measuring a point
replaces it in the table (after a reflow, say). The earlier reading is kept in
the database.

Click any row to fill in its point: the TODO rows are a checklist.

## Known-good readings

Readings become the reference for their board number in two ways:

- Tick **This is a known-good board** while recording a good board.
- Or, once a repair is proven, press **Mark all known-good** to use that job's
  readings.

Every later job on the same board number compares against them. They're stored
in `jobs.db` with the jobs, so they never leave the bench.

## In the report

The job report gets a **Measurements** table: each measured point, its reading,
the known-good value and the result. TODO points stay out of it.
