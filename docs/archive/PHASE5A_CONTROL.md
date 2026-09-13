# Phase 5a: scenario index, a detector correction, and the control experiment

Three scenarios have now been run under the four signal policies, and one
measurement defect was found and fixed between them. This document is the
authoritative index of which is which, and of what the results do and do not
support.

> **Simulated results throughout.** Under these simulation scenarios only. Not a
> measurement of real ambulance performance or real traffic in Bengaluru. No
> ambulance GPS trace, dispatch record or observed signal timing was used,
> because none was available.

> **Superseded by [`FINAL_RND_REPORT.md`](FINAL_RND_REPORT.md).** This document's
> replay-based control was later replaced by a cleaner fixed-schedule control
> with no replay override, and extended from one seed to five. The five-seed
> result changes the conclusion's strength: the traffic-side effect has **no
> stable sign**, and seed 42's large negative — reported below — was one draw
> from a distribution spanning -47,918 to +75,979 s. Kept as the record of how
> the control was first built and what it first showed.

Companions: [`EMS_SIGNAL_POLICIES.md`](../EMS_SIGNAL_POLICIES.md) (how the policies
work) · [`COUNTERFACTUAL_REPLAY.md`](../COUNTERFACTUAL_REPLAY.md) (the original
Phase 5 result)

---

## 1. The three scenarios

Each has its own trip config, its own result files and its own provenance
record. **No earlier result file has been overwritten.**

| | Phase 3 trip | Phase 5 trip | Phase 5a trip (Candidate D) |
|---|---|---|---|
| Label | `baseline` | `signalised` | `two_signal` |
| Origin | `312063814#2` | `312063814#2` | **`1311812959#0`** |
| Destination | `491889864#5` | `1411121774#1` | **`1196514116#0`** |
| Depart | 600 s | 600 s | 600 s |
| Route | 29 edges | 16 edges | **11 edges, 2,435 m** |
| Traffic lights on route | **0** | 2 | 2 |
| Actionable | 0 | 1 | **2** |
| Green fractions | — | 1.000, 0.600 | **0.267, 0.433** |
| Ambulance stopped by a signal? | no | no | **yes, once** |
| Result | untestable | 0.00 s saved | **12.5 s saved** |

Run them with `scripts/run_counterfactual.py --trip {baseline,signalised,two_signal}`.
Output files are namespaced by trip, so all three coexist.

### Why the trip changed twice

The Phase 3 trip's route passes **no traffic lights at all** — the router sends
it down a residential rat-run. Signal priority is untestable on it.

The Phase 5 trip passes two, but on movements that give the ambulance green in
all four phases at one (green fraction 1.000) and its least red-exposed movement
set at the other (0.600). One actionable signal, passed on green, and all four
policies returned an identical 205.5 s.

### How the Phase 5a trip was chosen

Against criteria fixed **before any policy was run** and referring only to route
geometry and signal timing — never to time saved:

1. at least two actionable traffic lights on the route,
2. each with baseline green fraction < 0.70 for the ambulance's own movement,
3. the NORMAL baseline must actually stop the ambulance at a signal.

The count in criterion 1 was originally **three**. A per-movement audit of all
eight traffic lights showed that only two of them have any movement below 0.70
green — `joinedS_` (minimum 0.267) and `GS_cluster` (minimum 0.433); the other
six are single-movement signals holding 0.889–0.911. **No route can pass three**,
so the count was relaxed to two and the 0.70 threshold, which carries the
mechanism, was kept. All 90 network entry/exit pairs were routed with duarouter
and scored on that basis; 18 reach two signals below 0.70, and this is the
shortest.

Nothing about the network, demand, vehicle behaviour, signal programs or routing
was changed to obtain any of this.

---

## 2. The detector correction

**Phase 5 reported `signal_wait_events: []` in all four runs and concluded the
ambulance "never halts within 60 m of a traffic light on its route". That
conclusion did not follow, because the detector could essentially never fire.**

The check computed distance as
`getDrivingDistance(ambulance, approach_edge, 0.0)` — the distance to the
**start** of the approach edge. The ambulance queues at the **stop line**, at the
edge's far end. Once it is on that edge the target is behind it and TraCI returns
`INVALID_DOUBLE_VALUE` (−2³⁰), so the `0 <= d <= 60` test fails. The only way the
old detector could fire was a queue spilling back onto an *earlier* edge.

Confirmed directly by a read-only probe of the Phase 5a NORMAL run:

```
halt at t = 671.0 s, edge 1393474724#1, lane position 7.6 m
  distance as previously computed:  -1073741824.0   (INVALID_DOUBLE_VALUE)
  distance to the stop line:                 8.2 m
  GS_cluster state: rrrrGy  ->  index 3, the ambulance's own link, is RED
```

The fix measures to the stop line. It is confined to the measurement:
`observation.distance_to_tls_m`, which the **policies** consume, is deliberately
left unchanged, so policy activation timing is identical to Phase 5 and the runs
remain comparable.

**What this does and does not overturn.** Phase 5's headline — 0.00 s saved on
the `signalised` trip — rested on two arguments. The `signal_wait_events` one is
void. The other is independent and stands: the ambulance's approach edge took
3.5 s in both NORMAL and EMS_FULL_PREEMPTION, which is a free-flow traversal.
The Phase 5 conclusion is probably still correct; one of its two supporting
arguments is not. **The Phase 5 result files still contain the old, uncorrected
`signal_wait_events` field** and have not been rewritten. Re-running the
`signalised` trip under the corrected detector would settle it.

---

## 3. Phase 5a result (Candidate D, seed 42)

| Policy | Ambulance travel | Time saved | Wait | Stops | Transitions | Signal changes | Conflicts | Teleports |
|---|---|---|---|---|---|---|---|---|
| NORMAL | **184.5 s** | — | 5.5 s | 1 | 0 | 0 | 0 | 0 |
| EMS_NEXT | **172.0 s** | **+12.50 s (6.78%)** | 0.0 s | 0 | 12 | 4 | 0 | 0 |
| EMS_ROLLING | **172.0 s** | **+12.50 s (6.78%)** | 0.0 s | 0 | 12 | 4 | 0 | 0 |
| EMS_FULL_PREEMPTION | **172.0 s** | **+12.50 s (6.78%)** | 0.0 s | 0 | 20 | 6 | 0 | 0 |

Attribution puts 11.5 s of the 12.5 s at `GS_cluster` — the one signal that
actually stopped the ambulance — 0.0 s at `joinedS_`, which was passed on green
despite the lower green fraction, and 1.0 s unattributed on non-signal edges.

**And the traffic side came out negative**: every policy *reduced* network-wide
time loss, by 25,450 s (EMS_NEXT), 36,598 s (EMS_ROLLING) and 33,433 s
(EMS_FULL_PREEMPTION), with mean time loss per vehicle down ~10%.

That is not plausible as an effect of prioritising one vehicle in ~2,900, and it
is the opposite sign to Phase 5, where the same policies **cost** 17,270–33,575 s.
A number that surprising is a reason to run a control, not a result to publish.

---

## 4. The control experiment — why, and how

**Question.** Is the traffic-side improvement caused by (A) the ambulance and its
interaction with traffic, or (B) the signal perturbation itself, applied to
netconvert's unoptimised fixed-time plans?

**Design.** Each policy is run once with the ambulance, recording the state of
every watched traffic light at every step. Those timelines are then replayed,
step for step, into runs whose demand is identical **except that the ambulance is
removed**. The perturbation is reproduced exactly; the ambulance's influence on
traffic is gone.

```bash
python scripts/run_control.py --seed 42
```

**Reproduction check.** The control script's own loop reproduces all four
published Phase 5a runs exactly — same vehicles completed, same total time loss
to the centisecond — so the recorded timelines are the real ones.

| Recorded run | Vehicles completed | Total time loss | Matches published |
|---|---|---|---|
| NORMAL | 2,897 | 271,976.19 s | yes |
| EMS_NEXT | 2,926 | 246,525.86 s | yes |
| EMS_ROLLING | 2,926 | 235,378.26 s | yes |
| EMS_FULL_PREEMPTION | 2,896 | 238,542.87 s | yes |

**The perturbations are real and distinct.** Against NORMAL's timeline, the
recorded states differ on a large fraction of steps:

| Policy | `joinedS_` | `GS_cluster` |
|---|---|---|
| EMS_NEXT | 3,519 / 7,799 (45.1%) | 6,494 / 7,799 (83.3%) |
| EMS_ROLLING | 3,519 / 7,799 (45.1%) | 6,529 / 7,799 (83.7%) |
| EMS_FULL_PREEMPTION | 3,959 / 7,799 (50.8%) | 6,575 / 7,799 (84.3%) |

### The approximations, stated exactly

Exact equivalence is **not** achievable, because the policies read ambulance
position. Three departures, none of them hidden:

1. **The control demand is the Phase 5a demand minus exactly one vehicle.**
   Every other vehicle, type, route and departure time is unchanged.
2. **Replay writes signal states directly** rather than selecting phases the way
   a policy does. Every replayed state is validated against its program's phase
   set — **0 states rejected** in every run — so no state occurs that the program
   could not have produced. The mechanism nevertheless differs from a live
   policy, and it is not neutral: measured against a no-replay ambulance-free
   run, replay alone moves total time loss by **+9,925 s**. That is comparable to
   the effects under test, which is why **NORMAL is recorded and replayed too**.
   Every control run goes through the identical replay path, so the only thing
   differing between them is the content of the timeline.
3. **SUMO is deterministic but chaotic.** Removing the single ambulance, with no
   replay, changes total time loss by **−2,602 s** and vehicles completed by
   −15 on its own. Differences of a few thousand seconds are inside that.

---

## 5. Control results (no ambulance anywhere, seed 42)

All runs: 3,900 s simulated, 7,800 steps, 0 signal conflicts, 0 replayed states
rejected, 0 incomplete ambulance trips (there is no ambulance).

| Run | Vehicles | Total travel | Total waiting | Total time loss | Mean loss/veh | Max queue | Mean queue | Teleports |
|---|---|---|---|---|---|---|---|---|
| control_NORMAL | 2,909 | 792,244 s | 188,872 s | **279,299 s** | 96.01 s | 202.8 m | 31.68 m | 1 |
| control_EMS_NEXT | 2,902 | 767,714 s | 167,879 s | **255,740 s** | 88.13 s | 202.6 m | 32.04 m | 2 |
| control_EMS_ROLLING | 2,871 | 782,623 s | 184,770 s | **274,138 s** | 95.48 s | **393.0 m** | 32.62 m | 4 |
| control_EMS_FULL_PREEMPTION | 2,899 | 750,824 s | 154,601 s | **239,117 s** | 82.48 s | 202.8 m | 33.14 m | 2 |

Paired against `control_NORMAL`:

| Policy | Vehicles Δ | Total travel Δ | Total waiting Δ | Total time loss Δ | Mean loss Δ |
|---|---|---|---|---|---|
| EMS_NEXT | −7 (−0.24%) | −24,531 s (−3.10%) | −20,994 s (−11.12%) | **−23,559 s (−8.44%)** | −7.89 s (−8.21%) |
| EMS_ROLLING | −38 (−1.31%) | −9,621 s (−1.21%) | −4,103 s (−2.17%) | **−5,161 s (−1.85%)** | −0.53 s (−0.55%) |
| EMS_FULL_PREEMPTION | −10 (−0.34%) | −41,421 s (−5.23%) | −34,271 s (−18.14%) | **−40,182 s (−14.39%)** | −13.53 s (−14.09%) |

---

## 6. The answer

**The negative traffic-side effect persists with no ambulance in the network.**
Cause **B**: the signal perturbation itself, not the ambulance.

Side by side, total time loss change:

| Policy | With ambulance | Control, no ambulance |
|---|---|---|
| EMS_NEXT | −25,450 s | **−23,559 s** |
| EMS_ROLLING | −36,598 s | **−5,161 s** |
| EMS_FULL_PREEMPTION | −33,433 s | **−40,182 s** |

Same sign in every case, and for EMS_NEXT and EMS_FULL_PREEMPTION the same order
of magnitude. The ambulance is not needed to produce the effect.

**Interpretation.** netconvert's fixed-time programs are generated, not
optimised. Holding and truncating phases at two junctions perturbs them heavily —
45–84% of steps — and on this demand that perturbation happens to move traffic
towards a better operating point. The improvement is a property of an
unoptimised baseline signal plan being disturbed, not of emergency-vehicle
priority.

**EMS_ROLLING is the tell.** Its control effect (−5,161 s) is seven times smaller
than its with-ambulance effect (−36,598 s), and it is the one run whose maximum
queue nearly doubles (393.0 m against ~203 m elsewhere) with four teleports. The
response to perturbation is erratic in both sign-magnitude and queueing, which is
what a chaotic response to disturbing a fragile fixed-time plan looks like — not
a mechanism that could be engineered for.

### What is supported

- On this scenario and seed, the policies removed the one signal stop the
  ambulance experienced and saved 12.5 s, attributed to `GS_cluster`.
- The policies ran without a single signal conflict, in every run, in every
  scenario.
- The network-wide traffic improvement observed under the EMS policies is
  **reproducible without any ambulance** and is therefore not attributable to
  emergency-vehicle priority.

### What is NOT supported

- **That EMS signal priority improves general traffic.** It does not follow from
  this data, and the control is the reason.
- That the traffic effect is a real-world effect at all. It is most likely an
  artefact of unoptimised generated signal timing, an ESTIMATED_DATA input.
- That 12.5 s is a reliable estimate of what priority saves. One seed; the
  ambulance's seed-to-seed standard deviation was 1.14 s in Phase 4, but the
  traffic side is unreplicated and is the anomalous part.
- Any claim about Bengaluru. Demand, vehicle behaviour and signal programs are
  all estimated.
- That the three policies differ in ambulance benefit. All three returned
  172.0 s; with one actionable stop there is nothing to separate them.

---

## 7. Limitations

1. **One seed.** Seeds 43–46 have not been run, for any scenario.
2. **The replay mechanism is not neutral** (+9,925 s on its own). Held constant
   across all control runs, so between-control differences are valid, but the
   control is not on the same absolute footing as the with-ambulance runs.
3. **Removing one vehicle is itself a perturbation** (−2,602 s, −15 vehicles).
4. **Signal programs remain netconvert-generated ESTIMATED_DATA.** This is now
   load-bearing: the anomaly under investigation is most likely a property of
   that timing.
5. **Only one of the two actionable signals did anything.** The entire 12.5 s
   rests on a single stop at `GS_cluster`.
6. **The Phase 5 result files retain the uncorrected `signal_wait_events`**, and
   the `signalised` trip has not been re-run under the fixed detector.
7. **1,375 of 1,515 edge speeds are SUMO defaults**; time loss is measured
   against free-flow and is over-estimated on those edges.
8. `way/1351994264` remains operational-status UNRESOLVED and is included, as in
   every prior phase.

---

## 8. What this suggests next

Before seeds 43–46 expand any of these numbers: the traffic-side metric is
currently measuring the fragility of the baseline signal plan more than it is
measuring policy cost. A perturbation control that is *not* an EMS policy — the
same phases held and truncated on a fixed schedule with no ambulance logic at
all — would establish how much of the variance is perturbation-generic. Until
then, the traffic-side cost or benefit of a priority policy in this model should
be treated as uninterpretable.

Machine-readable: [`control/`](../data/processed/silk_board_v1/control/) ·
[`counterfactual/`](../data/processed/silk_board_v1/counterfactual/)
