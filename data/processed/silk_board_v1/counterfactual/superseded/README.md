# Superseded result files

These are **not** failed or wrong runs. Each is an earlier measurement of an
experiment that was later re-measured after the signal-wait detector gained the
red/green classification (`stopped_by_signal`), which distinguishes a halt
*caused by* a signal from a halt that merely happened *near* one.

The simulation itself is unchanged and deterministic: the re-measured runs
reproduce the same travel times, waiting times and transition counts. Only the
recorded detail differs.

They are kept because the project's rule is that no experimental result is
overwritten, and because the difference between these files and their successors
is itself the audit trail of the detector correction.

| File suffix | What it is |
|---|---|
| `_pre_red_green_classification` | Measured with the corrected stop-line distance, but before halts were classified red vs green |

The original **Phase 5** (`signalised`) files measured with the *defective*
detector are the unsuffixed `seed42_*.json` files in the parent directory, and
are likewise unmodified.

## `_unused_lane_blockage` — Phase 8 disturbance runs, invalid

These 19 runs were produced with a disturbance that **did nothing**. It blocked
`lane_index=0` on the signal approach, and measurement later showed that lane
carries **zero vehicles** — all traffic uses lane 1. Vehicles-arrived was
byte-identical with and without the incident across every seed and policy, which
is what finally exposed it.

They are kept because they are the evidence for that defect: the identical
network totals are the proof that the instrument, not the scenario, was at
fault. They must not be read as "a disturbance that had no effect".

The corrected disturbance obstructs the lane traffic actually uses and delays
the seed-42 baseline ambulance by ~98 s.
