# Results

Simulated results only. Not a measurement of real ambulance performance in
Bengaluru — see [`LIMITATIONS.md`](LIMITATIONS.md). Method:
[`METHOD.md`](METHOD.md).

Every figure below comes from a committed file under `data/processed/`. Where the
2026-09-14 audit changed a number, both the old and the new one are given, with
the reason.

---

## 1. The headline, with its condition attached

**Signal priority is worth little when the ambulance is already moving, and
substantially more when congestion has put a queue in front of it.**

| Scenario | NORMAL trip | Best policy saves |
|---|---:|---:|
| Free-flowing approach | 184.5 s | **≈ 12 s** (6.8%) |
| With the modelled queue-producing disturbance | 282.5 s | **≈ 80 s** (28%) |

Quoting the second figure alone would describe a situation the first one
contradicts. What priority does is recover time an ambulance loses *to signals
and to the queues they create* — so when there is no such loss, there is nothing
to recover.

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

Read plainly:

- **Coordinating a look-ahead window is worth about 14 s** over preempting only
  the next signal. That is the one large effect among the policies.
- **Removing the distance limit entirely adds about 1 second** on top of that.
- **The extra aggression in `EMS_FULL_PREEMPTION` buys nothing at all.** Cutting
  cross-traffic greens to 3 s and holding for up to 600 s does not beat the same
  scope with ordinary parameters — the difference is −0.3 s, inside the noise —
  while it leaves up to 40 more vehicles unfinished (§5).

> **Correction to the earlier framing.** The pre-audit report said EMS_ROLLING
> "captures 98.8% of full preemption's benefit". The arithmetic was right and the
> precision was not: the per-seed ratios are 97.5–100%, so this is "about 99%,
> and the gap is around a second". Three significant figures on a ratio of two
> five-sample means claims a resolution the data does not have.

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

- **Signal safety.** 311,960 applied signal states and every change between them
  checked against the junction's own foe matrix across the 25 corrected runs:
  **0 conflicting protected greens, 0 unclear swaps.** The structural argument
  (policies only select among netconvert's phases) is now also a tested property.
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

## 8. The free-flowing scenario

The second matrix — same design, no disturbance — is unchanged from the pre-audit
runs and has not been re-run under the corrected code:

| Policy | Time saved |
|---|---:|
| `EMS_NEXT` | 12.2 ± 1.0 s |
| `EMS_ROLLING` | 12.5 ± 0.4 s |
| `EMS_FULL_PREEMPTION` | 12.7 ± 0.6 s |

The cohort re-analysis *was* run on it, with the same outcome as the incident
matrix: 2 of 15 pairs change sign between the biased and paired views, and no
policy's traffic-side effect is distinguishable from zero
(`data/processed/silk_board_v1/paired_cohort_two_signal.json`).

Given §2 — where the corrected code reproduced the incident matrix to within a
third of a second — there is no reason to expect these to move either, but they
have not been recomputed and are labelled as pre-audit numbers rather than
quietly carried forward.

---

*All figures are simulated. `data/processed/` holds the records they come from;
`docs/archive/FINAL_RESULTS.md` holds what was reported before the audit.*
