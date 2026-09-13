# EMS Black Box — final results

Five seeds, four policies, one disturbed scenario. Every statement below is
tagged **OBSERVED** (a number SUMO produced), **ASSUMED** (an input this project
chose because no source existed), or **INTERPRETED** (what the two together
appear to mean). The rules for those labels are in
[`INTERPRETATION_FRAMEWORK.md`](../INTERPRETATION_FRAMEWORK.md).

> Nothing here is a measurement of real ambulance performance or real traffic in
> Bengaluru. No ambulance GPS trace, dispatch record, signal timing or traffic
> count was used, because none was available.

---

## 1. Executive result

**OBSERVED.** Under a simulated lane-obstruction scenario on the Silk Board
network, with everything held identical except the signal policy:

| Policy | Ambulance travel (mean) | Time saved vs NORMAL | sd | Range |
|---|---|---|---|---|
| NORMAL | **284.0 s** | — | 2.32 | 282.0–287.0 |
| EMS_NEXT | 218.9 s | **65.1 s** | 3.13 | 62.5–68.5 |
| EMS_ROLLING | 204.6 s | **79.4 s** | 2.07 | 77.5–82.5 |
| EMS_FULL_PREEMPTION | 203.6 s | **80.4 s** | 2.07 | 78.0–83.0 |

**OBSERVED.** The ordering EMS_NEXT ≤ EMS_ROLLING ≤ EMS_FULL_PREEMPTION holds in
**5 of 5 seeds**, and leave-one-out analysis shows no single seed drives it.

**OBSERVED.** EMS_FULL_PREEMPTION buys **1.0 s** more than EMS_ROLLING on average
— and **0.0 s** in seed 46 — while adding a mean **+11,562 s** of traffic-side
time loss and **+1.2** network teleports.

**INTERPRETED.** On this scenario, EMS_ROLLING captures **98.8%** of the
ambulance benefit that full preemption achieves. The remaining 1 s does not
appear to justify the additional disruption, and in one seed there is no
remaining second to buy.

## 2. All five seeds

**OBSERVED.** Ambulance travel time (s):

| Seed | NORMAL | EMS_NEXT | EMS_ROLLING | EMS_FULL |
|---|---|---|---|---|
| 42 | 282.5 | 220.0 | 204.0 | 202.5 |
| 43 | 286.0 | 217.5 | 205.5 | 204.0 |
| 44 | 282.0 | 219.5 | 204.5 | 203.0 |
| 45 | 287.0 | 218.5 | 204.5 | 204.0 |
| 46 | 282.5 | 219.0 | 204.5 | 204.5 |

Time saved (s), per seed — shown so nothing hides inside a mean:

| Policy | 42 | 43 | 44 | 45 | 46 | FULL − ROLLING |
|---|---|---|---|---|---|---|
| EMS_NEXT | 62.5 | 68.5 | 62.5 | 68.5 | 63.5 | — |
| EMS_ROLLING | 78.5 | 80.5 | 77.5 | 82.5 | 78.0 | — |
| EMS_FULL | 80.0 | 82.0 | 79.0 | 83.0 | 78.0 | +1.5 / +1.5 / +1.5 / +0.5 / **+0.0** |

**OBSERVED.** No outlier at \|z\| > 2 for any policy.

## 3. Cross-seed statistics

**OBSERVED.** Paired differences against each seed's own NORMAL run:

| Metric | EMS_NEXT | EMS_ROLLING | EMS_FULL |
|---|---|---|---|
| Travel saved (s) | 65.1 ± 3.13 | 79.4 ± 2.07 | 80.4 ± 2.07 |
| Waiting saved (s) | **56.3 ± 2.31** | **56.3 ± 2.31** | **56.3 ± 2.31** |
| Time-loss reduction (s) | 65.06 ± 3.20 | 79.48 ± 2.14 | 80.41 ± 2.20 |
| Stop reduction | 2 (all seeds) | 2 (all seeds) | 2 (all seeds) |
| Teleport delta | −0.4 ± 0.55 | 0.0 ± 1.23 | **+1.2 ± 1.92** |
| Traffic Δ time loss (s) | −4,288 ± 31,007 | +5,586 ± 39,079 | +17,148 ± 34,943 |

**OBSERVED — and this is the most informative row.** *Waiting-time saved is
identical across all three policies within every seed.* All three eliminate
exactly the same signal wait. Whatever separates them is not waiting at the stop
line.

## 4. Where the policies actually differ

**OBSERVED.** Per-edge traversal, seed 42 (s):

| Route edge | NORMAL | EMS_NEXT | EMS_ROLLING | Saved by NEXT | Saved by ROLLING |
|---|---|---|---|---|---|
| `92196679#0` (964 m, upstream) | 84.5 | 68.5 | 54.5 | +16.0 | **+30.0** |
| `1393474724#1` (16 m, obstructed signal approach) | 80.5 | 34.5 | 31.5 | +46.0 | +49.0 |

**OBSERVED.** At the signal itself the three policies are nearly equal
(attributed delay reduction 47.7 / 48.9 / 48.9 s). The gap between EMS_NEXT and
EMS_ROLLING lives on the **upstream** edge, and shows up in the *unattributed*
residual: seed 42, 16.5 s under EMS_NEXT versus 29.5 s under EMS_ROLLING.

**INTERPRETED.** EMS_ROLLING's advantage is not that it clears the stop line
better. It holds the signal earlier and over a wider window, so the queue that
had spilled back onto the 964 m upstream approach discharges sooner, and the
ambulance meets less of it.

**Methodological limitation, OBSERVED.** The attribution method credits a signal
only for its own approach edge, so it **under-credits a policy whose benefit
comes from upstream queue discharge**. The residual is correctly not assigned to
any intersection — but the intersection ranking therefore understates the
difference between EMS_NEXT and EMS_ROLLING by roughly 13 s.

## 5. Traffic-side cost

**OBSERVED.** Mean Δ total time loss: EMS_NEXT −4,288 s, EMS_ROLLING +5,586 s,
EMS_FULL +17,148 s — with standard deviations of 31,007 / 39,079 / 34,943.

**INTERPRETED.** The standard deviation exceeds the mean in every case and every
range spans zero. This reproduces the Phase 5b finding: the traffic-side term has
**no stable sign** and remains `DIAGNOSTIC_ONLY`. The ordering of the means is
consistent with stronger preemption costing more, but at n=5 with this variance
that ordering is **not** established.

## 6. Teleport analysis

**OBSERVED.** Teleport delta vs NORMAL: EMS_NEXT −0.4 ± 0.55, EMS_ROLLING
0.0 ± 1.22, EMS_FULL **+1.2 ± 1.92** (per seed: +2, +4, +1, −1, 0).

**INTERPRETED.** The mean rises monotonically with preemption strength, which is
what one would expect if holding cross-traffic greens longer pushes some
approach into gridlock. But seed 45 is negative and the sd exceeds the mean, so
this is **suggestive, not established**. It matters because a teleported
vehicle's travel time is not a time anyone drove, and it sits inside the traffic
aggregate — so a policy that causes more teleports partly hides its own cost.

## 7. Intersection attribution

**OBSERVED.** Across all 15 policy runs:

| Traffic light | Mean recoverable delay | Non-zero in |
|---|---|---|
| `GS_cluster_10282769895_…` | **48.50 s** | **15 / 15** |
| `joinedS_12074449284_…` | **0.00 s** | **0 / 15** |

**OBSERVED.** `joinedS_` has the *lower* green fraction (0.267 vs 0.433) yet
contributes exactly zero in every run. The ambulance passes it on green.

**INTERPRETED.** Green fraction predicts *exposure*, not delay. One intersection
accounts for all recoverable ambulance delay in this scenario, consistently
across every seed and policy.

## 8. Disturbance consistency

**OBSERVED.** Single config hash `3c43dded423b95d7` across all 20 runs; applied
in every run. Baseline effect per seed: ambulance travel 282–287 s (undisturbed:
184–185 s), waiting 53.5–59.5 s (undisturbed: 5.5–7.5 s), 2 stops (undisturbed:
1), route queue 52–77 m, 18 vehicles halted at peak.

**INTERPRETED.** The disturbance behaves consistently and adds **97.5–102.0 s**
of ambulance delay in every seed (per seed: +98.0, +102.0, +97.5, +102.0, +98.0).
It is a genuine, replicated perturbation.

## 9. Integrity and adversarial review

**OBSERVED.** 16-point integrity gate: **PASS on all 5 seeds.** Nine adversarial
probes designed to *break* the result: **9/9 SURVIVED**, including
`disturbance_is_not_inert` — the probe added after a disturbance that changed
nothing passed a 15-point gate undetected.

Scene validation: 577 checks, 0 failures, 132,778 vehicle samples.

## 9b. What every number above is conditional on

**ASSUMED.** None of the following is sourced; each was chosen by this project
because no measurement was available, and each conditions every result above.
Full table with consequences in
[`INTERPRETATION_FRAMEWORK.md`](../INTERPRETATION_FRAMEWORK.md).

| Input | Class | Why it matters here |
|---|---|---|
| Signal programs — netconvert-generated, uniform 90 s cycle | `ESTIMATED_DATA` | The most load-bearing input. It sets the red the ambulance waits at, and therefore the delay any policy can recover. All 65–80 s reported above are recovered from *this* assumed timing. |
| Demand scale 0.5, vehicle mix (50% motorcycle etc.) | `ESTIMATED_DATA` | Sets how much traffic the ambulance meets, and drives saturation flow into every queue figure. |
| The lane obstruction — location, lane, timing, severity | `SIMULATED_SCENARIO` | A hypothetical declared by this project. **Not a record of any real incident at Silk Board.** Without it the same policies save ~12.5 s, not ~80 s. |
| Ambulance origin, destination, departure time | `ESTIMATED_DATA` | A configuration choice, not a dispatch record. |
| 1,375 of 1,515 edge speeds are SUMO defaults; 166 at ≥ 80 km/h | `ESTIMATED_DATA` | Time loss is measured against free-flow, so it is over-estimated on those edges. |
| Road elevation (OSM `layer` × 6 m), building heights (~99% estimated) | `ESTIMATED_DATA` | 3D scene only. No height on screen is a measurement. |
| Vehicle 3D models | authored, not GLB assets | Appearance only; lengths and widths come from the vTypes. |

**Nothing in this project is `VERIFIED_REAL_DATA`.**

## 10. Recommended research conclusion

**INTERPRETED, and conditional on every assumption in
[`INTERPRETATION_FRAMEWORK.md`](../INTERPRETATION_FRAMEWORK.md):**

1. Under this simulated disturbance, EMS signal priority reduced simulated
   ambulance travel time by **65–80 s** against a paired 284 s baseline,
   consistently across five seeds.
2. **Coordinated multi-signal priority (EMS_ROLLING) is the defensible choice on
   this scenario.** It captures 98.8% of full preemption's benefit. Full
   preemption's extra ~1 s comes with a mean +11,562 s traffic term and +1.2
   teleports — and in one seed buys nothing at all.
3. The benefit mechanism is **queue discharge upstream**, not merely a green at
   the stop line. This is the substantive finding, and it is invisible to a
   metric that only looks at the signal approach.
4. The traffic-side term **cannot** be used to cost these policies. It has no
   stable sign, and two controls reproduced changes of this magnitude with no
   ambulance in the network.

**NOT concluded, and not claimed anywhere:** any statement about real ambulances
in Bengaluru; that EMS priority improves general traffic; that these savings
transfer to another route, demand level or signal plan; or any statistical
significance — five seeds are reported as descriptive statistics only.
