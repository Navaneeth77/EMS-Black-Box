# Results

Simulated results only. Not a measurement of real ambulance performance in
Bengaluru — see [`LIMITATIONS.md`](LIMITATIONS.md). Method:
[`METHOD.md`](METHOD.md).

Every figure below comes from a committed file under `data/processed/`. Where the
2026-09-14 audit changed a number, both the old and the new one are given, with
the reason.

**No result here is a test of historically sourced signal timing.** The one
sourced figure — a 450 s existing cycle at Central Silk Board — is verified and
used only by the HISTORICAL_DEMO replay; why it cannot enter the counterfactual
is in [`METHOD.md`](METHOD.md) §3.

---

## 1. The headline, with its condition attached

**Signal priority is worth little when the ambulance is already moving, and
substantially more when congestion has put a queue in front of it.**

Four congestion levels, five seeds each, everything else identical (§9):

| Congestion | NORMAL trip | Best policy saves | |
|---|---:|---:|---|
| None | 184.5 ± 0.4 s | **12.4 ± 0.4 s** | 6.7% |
| Obstruction, `low` | 191.2 ± 3.8 s | **11.7 ± 3.5 s** | 6.1% |
| Obstruction, `medium` | 284.0 ± 2.3 s | **80.5 ± 2.1 s** | 28.3% |
| Obstruction, `high` | 520.7 ± 29.4 s | **86.9 ± 26.8 s** | 16.7% |

Quoting the large figures alone would describe a situation the small ones
contradict. What priority does is recover time an ambulance loses *to signals and
to the queues they create* — so when there is no such loss, there is nothing to
recover, and when the corridor is saturated there is more loss than priority can
reach.

**The range 65–80 s is the benefit under one particular modelled obstruction. It
is not a baseline.** The baseline — what priority is worth with the corridor
clear — is about 12 s.

## 2. The counterfactual matrix, with the disturbance

Five seeds × five policies, one scenario, paired against NORMAL of the same seed.
Ambulance time saved, in seconds:

| Policy | Pre-audit | **Corrected** | Per seed (corrected) |
|---|---:|---:|---|
| `EMS_NEXT` | 65.1 ± 3.1 | **65.1 ± 3.2** | 61.5, 69.0, 63.5, 68.0, 63.5 |
| `EMS_ROLLING` | 79.4 ± 2.1 | **79.3 ± 2.1** | 78.0, 80.5, 77.5, 82.5, 78.0 |
| `EMS_FULL_SCOPE` | — *(did not exist)* | **80.5 ± 2.1** | 79.5, 83.0, 79.0, 82.5, 78.5 |
| `EMS_FULL_PREEMPTION` | 80.4 ± 2.1 | **80.2 ± 1.7** | 78.5, 81.5, 79.5, 82.5, 79.0 |

**The ambulance-side result survived the audit.** The corrected code — a rebuilt
state machine, a corrected traffic-light mapping, tracked phase timing, a
coordination rule — reproduces the published numbers to within a third of a
second on every policy. The defects the audit found were real (§4), and none of
them was moving this number.

Source: `data/processed/silk_board_v1/counterfactual/corrected_inc_two_signal_seed4*.json`.
The pre-audit runs are beside them under their original names, untouched.

## 3. What activation scope is actually worth

The pre-audit matrix could not answer this. `EMS_FULL_PREEMPTION` differs from
`EMS_ROLLING` in **three** parameters at once — unlimited scope, a 3 s minimum
green instead of 5 s, and a 600 s hold ceiling instead of 60 s — so the 1 s
between them was not the value of scope, it was the value of all three together
in unknown proportion.

`EMS_FULL_SCOPE` is `EMS_ROLLING` with the distance test removed and nothing else
changed. Paired per seed:

| Comparison | What differs | Per seed | Mean | Verdict |
|---|---|---|---:|---|
| `FULL_SCOPE − ROLLING` | activation scope only | +1.5, +2.5, +1.5, 0.0, +0.5 | **+1.2 s** | distinguishable from zero (se 0.44) |
| `FULL_PREEMPTION − FULL_SCOPE` | 3 s min green, 600 s ceiling | −1.0, −1.5, +0.5, 0.0, +0.5 | **−0.3 s** | **not** distinguishable (se 0.41) |
| `ROLLING − NEXT` | look-ahead window vs next signal | +16.5, +11.5, +14.0, +14.5, +14.5 | **+14.2 s** | distinguishable (se 0.80) |

Read plainly, **and only of this scenario**:

- **Coordinating a look-ahead window is worth about 14 s here** over preempting
  only the next signal. That is the one large effect among the policies — and it
  exists only because there is a queue for the extra signals to discharge.
- **Removing the distance limit entirely adds about 1 second** on top of that.
- **The extra aggression in `EMS_FULL_PREEMPTION` buys nothing at all.** Cutting
  cross-traffic greens to 3 s and holding for up to 600 s does not beat the same
  scope with ordinary parameters — the difference is −0.3 s, inside the noise —
  while it leaves up to 40 more vehicles unfinished (§5).

> **The 14 s is conditional, and the condition is not optional.** It belongs to
> this obstruction at this severity. With the obstruction off (§8) the same
> contrast is **+0.1 s (se 0.91)**; with a milder one **+0.1 s (se 0.40)**; with
> a four-times tighter one **0.0 s (se 0.42)** (§9). At three of the four
> congestion levels tested the three EMS policies are **indistinguishable from
> each other**. An earlier draft of this section stated the 14 s without that
> condition; it must not be quoted as a property of the policies.

> **Correction to the earlier framing.** The pre-audit report claimed EMS_ROLLING
> "captures 98.8% of full preemption's benefit". That precision is withdrawn and
> is not restated anywhere as a finding: the per-seed ratios are 97.5–100%, so
> the most that can be said is "about 99%, and the gap is around a second".
> Three significant figures on a ratio of two five-sample means claims a
> resolution the data does not have. The claim survives only in
> [`archive/FINAL_RESULTS.md`](archive/FINAL_RESULTS.md) as what was reported at
> the time.

## 4. What the audit changed

### 4a. The traffic metric was measuring different vehicles in each arm

`traffic_aggregate()` summed delay over "the vehicles that arrived" — a different
set in each arm. A policy that holds cross traffic longer leaves more of it
unfinished, and those are the vehicles it delayed most, so they left its own
total and it could look cheaper for being more expensive.

Re-analysing the frozen runs over a properly paired cohort
(`scripts/analyse_paired_cohort.py`, which reproduces the published metric to the
decimal before departing from it):

- **4 of 30 pairs change sign.** Seed 42 `EMS_NEXT` went from −12,548 vehicle-seconds
  ("an improvement") to +9,291 ("a cost"); it had completed 9 fewer vehicles.
- **Magnitudes change by up to 3×.** Seed 42 `EMS_FULL_PREEMPTION`: +20,123 biased
  against +66,393 paired, with 49 more vehicles left unfinished.

### 4b. A traffic light's two movements were merged into one

The route makes two controlled movements through one joined signal cluster. The
mapping kept only the first movement's links, merged all four link indices into
one entry, and labelled it with the wrong approach edge — so per-intersection
attribution credited the wrong edge and the policy asked for a phase satisfying
both movements at once without anything saying that was the intention.

The mapping now records two encounters, each with its own links, approach edge
and route position. The policy then needs an explicit rule for what to ask a
signal that has two of the ambulance's movements on it, and has one: **request a
phase serving every movement still ahead of the ambulance at that signal**, with
a documented fallback when the program has no such phase.

### 4c. Elapsed phase time, the release, and the audit trail

- Phase elapsed time was inferred as `programmed duration − remaining`, which
  stops being true the moment a policy changes a duration — which is what these
  policies do. Now observed and recorded.
- `_release()` called `setProgram()` while claiming to force nothing. Measured
  against SUMO 1.27.1: with the program already active it is a no-op, and every
  traffic light in both networks has exactly one program. Removed.
- Audit records assigned the before and after signal states the same value in the
  same breath, so no record could ever show a change. They are now resolved from
  the next step's observation.
- `min_green_s` meant both "never truncate below this" and "extend by this much
  each step while holding". Split.
- `NormalPolicy` deleted its own actionable-signal list at startup, so the control
  arm reported zero actionable signals — not because there were none.

**None of these changed the ambulance-side numbers** (§2). They changed what the
record says about how those numbers were obtained, which is the thing the project
exists to be able to show.

### 4d. A regression the audit introduced and then caught

The rebuilt state machine added a `RESTORING` state that waits for the signal to
leave the phase it was holding. SUMO reports no route index for a vehicle inside
a junction, and the relevance test read that gap as "the ambulance has passed" —
so a momentary blip now *guaranteed* the loss of the green the ambulance was
about to use. It cost `EMS_ROLLING` 50 s of its benefit on seed 42.

It was caught by comparing a corrected run against the frozen trace it was
supposed to reproduce, not by the test suite. The fix keeps the last known route
index across junction interiors. Recorded here because "the tests passed" was
true throughout.

### 4e. The last instance of the same defect, found by a stray log line

The audit's central fix was that a traffic light met twice on a route is two
encounters, not one (§4b). One place still keyed by `tls_id` alone: the halt
detector's map of stop-line positions. The route's two encounters at the
`joinedS_…` cluster approach on edges 19.55 m and 33.69 m long, and the second
overwrote the first — so the query for the first asked TraCI for a position 33.69 m
along a 19.55 m edge. TraCI answered `Position on lane invalid`, the caller
suppressed it, and that encounter silently dropped out of the halt record.

It surfaced as one stderr line per run during the `NO_INCIDENT_CONTROL` runs, and
it had been there through the whole published matrix. **It changed no reported
value**: it only degrades a diagnostic annotation, and the ambulance never halted
on that edge in any run of any matrix. Verified rather than argued — seed 42 was
re-run under the fix in both scenarios across four policies, and every ambulance
metric, vehicle count, teleport count and scenario hash came back identical.

Kept as a lesson rather than deleted: the defect was invisible to 570 tests and
to every result file, and what exposed it was a log line nobody had read.

## 5. The traffic side is a diagnostic, not a cost

On the corrected paired cohort, over five seeds:

| Policy | Paired cohort | Mean time-loss change | Distinguishable from zero? |
|---|---:|---:|---|
| `EMS_NEXT` | 2,830–2,889 vehicles | +0.05 ± 6.88 s/vehicle | no |
| `EMS_ROLLING` | 2,859–2,882 | −0.38 ± 6.51 | no |
| `EMS_FULL_SCOPE` | 2,847–2,910 | +1.85 ± 6.07 | no |
| `EMS_FULL_PREEMPTION` | 2,825–2,915 | +2.00 ± 6.26 | no |

(± is the standard error of the across-seed mean.)

This agrees with the two ambulance-free controls — Phase 5a's replay and Phase
5b's fixed schedule — which produced traffic-side changes of the same size with
no ambulance in the network at all. **The metric is measuring how sensitive
netconvert's unoptimised fixed-time plans are to being perturbed, not what
priority costs.** It stays labelled `DIAGNOSTIC_ONLY`.

What *is* visible in the cohort counts is the direction of the aggression trade:
`EMS_FULL_PREEMPTION` leaves up to **40 more vehicles unfinished** than NORMAL on
a seed, against ±20 for the narrower policies — for no ambulance-side gain over
`EMS_FULL_SCOPE`.

## 6. Where the time is actually recovered

Per-intersection attribution, averaged over five seeds (corrected matrix):

| Policy | At the stop line | Upstream queue | Unattributed | Upstream share |
|---|---:|---:|---:|---:|
| `EMS_NEXT` | 47.7 s | 17.0 s | 1.0 s | **26%** |
| `EMS_ROLLING` | 48.9 s | 30.1 s | 0.9 s | **38%** |
| `EMS_FULL_SCOPE` | 49.2 s | 30.6 s | 0.9 s | **38%** |
| `EMS_FULL_PREEMPTION` | 49.1 s | 30.6 s | 0.5 s | **38%** |

Roughly **a third of the benefit is not waiting at a red at all** — it is time the
ambulance would have spent in the queue *behind* the signal, on the edge before
the approach. The pre-audit attribution could not see this: it credited only the
approach edge and put the rest in an unattributed residual.

Attribution is only made where there is evidence for it — a queue recorded at
that signal while the ambulance was on that edge — and what is left over stays in
the unattributed bucket rather than being assigned to the nearest signal.

## 7. Safety, queues, and pairing

- **Signal safety.** Every applied signal state and every change between them
  checked against the junction's own foe matrix: **0 conflicting protected
  greens, 0 unclear swaps**, in every run of every matrix. Each run checks 15,598
  states, so the 25 corrected incident runs are **389,950**.

  > **Correction.** This section previously reported "311,960 applied signal
  > states … across the 25 corrected runs". 311,960 is the total for **20** runs;
  > 25 runs is 389,950. An arithmetic error in the report, not in the check — the
  > conflict count it was describing was and is zero.
- **Queue clearance.** Measured at each signal at the four moments the policy's
  timeline names. Across 60 encounters the queue at the actionable signals was
  0.8 vehicles when the green came and 0 when the ambulance arrived, clearing in
  4.5 s. These signals carry little standing queue in this scenario — the
  disturbance's queue is elsewhere on the route, which is consistent with §6,
  where the recovered time is upstream.
- **Pairing.** All five arms of each corrected seed share one scenario hash
  (`55322b7829193ee8` for seed 42) and differ in policy hash. The hash differs
  from the pre-audit one (`ff9fa74f5992d70d`) because the scenario identity now
  **includes the disturbance** — without it, an arm with a lane blocked and an arm
  without hashed identically and would have paired without complaint.

## 8. The free-flowing control, re-run

`NO_INCIDENT_CONTROL` is the same design with the disturbance switched off,
re-run under the corrected code on the same five seeds and the same vehicle
cohort (3,226 identical vehicle IDs per seed, byte-for-byte the demand the
incident matrix used).

| Policy | Travel time | Time saved | Per seed |
|---|---:|---:|---|
| `NORMAL` | 184.5 ± 0.4 s | — | 184.5, 184.0, 184.5, 185.0, 184.5 |
| `EMS_NEXT` | 172.4 ± 1.3 s | **12.1 ± 1.2 s** | 10.0, 13.0, 12.5, 13.0, 12.0 |
| `EMS_ROLLING` | 172.3 ± 1.1 s | **12.2 ± 1.3 s** | 12.5, 10.0, 13.5, 12.5, 12.5 |
| `EMS_FULL_SCOPE` | 172.1 ± 0.4 s | **12.4 ± 0.4 s** | 12.0, 12.0, 13.0, 12.5, 12.5 |

It reproduces the pre-audit free-flow matrix: NORMAL 184.5 ± 0.4 against
184.5 ± 0.4, `EMS_NEXT` 12.1 against 12.2, `EMS_ROLLING` 12.2 against 12.5. Those
numbers are no longer carried forward as pre-audit values.

**The mechanism is visible and it explains §3.** With no disturbance the NORMAL
ambulance makes **exactly one** signal stop — at the `GS_cluster` approach, in
all five seeds — losing 5.5–7.0 s of waiting. All three policies remove that one
stop, and all three land on ~172 s. There is nothing left for a wider look-ahead
window to buy, which is why the contrasts between the policies collapse here.

Traffic side, corrected paired cohort: `EMS_NEXT` −9.4 ± 4.9, `EMS_ROLLING`
−4.2 ± 4.4, `EMS_FULL_SCOPE` −7.7 ± 4.0 s per vehicle — none distinguishable from
zero, as in the incident matrix. Attribution puts only **8.7%** of the recovered
time upstream of the stop line, against 26–38% with the disturbance.

Source: `data/processed/silk_board_v1/counterfactual/NO_INCIDENT_CONTROL_*.json`.
0 signal conflicts, 0 ambulance teleports, 14 background teleports over 20 runs,
route identical to the incident matrix's in every run.

## 9. How the benefit depends on congestion severity

`CONGESTION_SEVERITY` scales the obstruction's discharge rate and nothing else —
same edge, same lane, same window, same mechanism (`METHOD.md` §1). `medium` **is**
the committed obstruction, and reproduces the corrected matrix of §2 to the
decimal on all twelve figures, which is how the sweep and the published result
are known to be the same scenario.

**Ambulance time saved, mean ± sd over five seeds:**

| Policy | none | `low` | `medium` | `high` |
|---|---:|---:|---:|---:|
| `NORMAL` trip | 184.5 ± 0.4 | 191.2 ± 3.8 | 284.0 ± 2.3 | 520.7 ± 29.4 |
| `EMS_NEXT` | 12.1 ± 1.2 | 11.4 ± 4.0 | **65.1 ± 3.2** | **86.9 ± 26.8** |
| `EMS_ROLLING` | 12.2 ± 1.3 | 11.5 ± 4.3 | **79.3 ± 2.1** | **86.9 ± 26.6** |
| `EMS_FULL_SCOPE` | 12.4 ± 0.4 | 11.7 ± 3.5 | **80.5 ± 2.1** | **86.0 ± 27.0** |

**The contrasts between the policies, which is what the sweep was for:**

| Contrast | none | `low` | `medium` | `high` |
|---|---:|---:|---:|---:|
| `ROLLING − NEXT` | +0.1 (se 0.91) | +0.1 (se 0.40) | **+14.2 (se 0.80)** | 0.0 (se 0.42) |
| `FULL_SCOPE − ROLLING` | +0.2 (se 0.46) | +0.2 (se 0.49) | **+1.2 (se 0.44)** | −0.9 (se 0.29) |

### What this changes

**The look-ahead advantage is not a property of the policy. It is a property of a
middle band of congestion.** `ROLLING − NEXT` is indistinguishable from zero with
no obstruction, indistinguishable from zero with a mild one, **+14.2 s** at the
committed severity, and **back to exactly zero** when the obstruction is four
times tighter. It is not monotone in congestion and it does not survive at either
end. An earlier draft reported the 14 s as though it were general; on this
evidence it is not.

The mechanism is visible in the runs. Under `high`, priority no longer clears the
corridor: the ambulance still waits 12.6 s and still makes **22.4 stops** with
`EMS_ROLLING` against 25.4 under `NORMAL` — on seed 42 it makes *more* stops with
priority than without (33 against 27) while still arriving 52.5 s earlier. The
time it recovers is queue discharge, not stop-line waiting, and **90% of the
attributed benefit is upstream** against 38% at `medium` and 1% at `low`:

| Attribution (mean) | none | `low` | `medium` | `high` |
|---|---:|---:|---:|---:|
| At the stop line | 11.6 s | 9.6 s | 48.9 s | 8.6 s |
| Upstream queue | 1.1 s | 0.1 s | 30.1 s | 77.5 s |
| Upstream share | 8.7% | 1.0% | 38.1% | **90.0%** |

Once nearly all of the recoverable time is upstream, *which* signals a policy
preempts stops mattering — every policy is discharging the same queue — and the
policies converge. That is the same reason they converge in free flow, arrived at
from the opposite direction: at one end there is nothing to recover, at the other
there is nothing a wider window recovers that the narrow one does not.

**`high` is also where the result becomes unstable.** Its per-seed savings are
+53.0, +109.5, +107.5, +63.0, +101.5 — a spread of 57 s, against 3 s at `medium`.
The standard deviation (±27) is a third of the mean. A number from this severity
should be quoted as "tens of seconds, highly variable", not as a value.

**`FULL_SCOPE − ROLLING` turns slightly negative** under `high` (−0.9 s, se 0.29,
4 of 5 seeds negative). Unlimited scope is marginally *worse* than a 700 m window
when the whole corridor is saturated. The effect is under a second and rests on
five seeds; it is reported because it is what the runs show, not because it is
established.

### Integrity

60 runs, all valid for headline use. 0 signal conflicts over 935,880 applied
states across the three severities, 0 SUMO collisions, 0 ambulance teleports, one
scenario hash per severity per seed with four distinct policy hashes, and the
identical ambulance route in every run of every experiment. Background teleports
rise with severity — 12 (`low`), 22 (`medium`), 35 (`high`) per 20 runs — and are
reported separately; no teleported vehicle enters a travel-time metric.

Source: `data/processed/silk_board_v1/counterfactual/CONGESTION_{LOW,MEDIUM,HIGH}_inc_*.json`.

---

*All figures are simulated. `data/processed/` holds the records they come from;
`docs/archive/FINAL_RESULTS.md` holds what was reported before the audit.*
