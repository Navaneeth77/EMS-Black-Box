# EMS Black Box — final R&D report

The complete research record for the simulation phases, written as the gate
before any 3D visualisation work begins.

> **Everything in this document is a simulated result.** No ambulance GPS trace,
> no dispatch record, no observed signal timing and no traffic count from
> Bengaluru was used, because none was available to this project. Nothing here
> is a measurement of real ambulance performance or real traffic, and no number
> in it may be quoted as one.

Companions: [`PHASE5A_CONTROL.md`](PHASE5A_CONTROL.md) ·
[`EMS_SIGNAL_POLICIES.md`](EMS_SIGNAL_POLICIES.md) ·
[`COUNTERFACTUAL_REPLAY.md`](COUNTERFACTUAL_REPLAY.md) ·
[`3D_READINESS.md`](3D_READINESS.md)

---

## 1. Research question

Does giving an ambulance signal priority reduce its travel time through a
congested urban junction — and can that reduction be *attributed* to the
priority policy rather than to anything else that differed between runs?

The second half is the actual research contribution. A single run showing an
ambulance taking N seconds under preemption says nothing, because there is no
statement of what it would have taken otherwise. The project is built around
**paired counterfactual replay**: the same network, demand, seed, vehicles and
ambulance trip, differing only in the signal policy, compared only against its
own paired NORMAL run.

A secondary question — what priority costs the rest of the network — was posed
at the outset and, as section 18 records, **could not be answered defensibly**
and is reported as a negative methodological result.

## 2. System architecture

Six stages, of which the first three and the last are complete:

| Stage | Component | Status |
|---|---|---|
| 1 | OpenStreetMap → SUMO network (`netconvert`) | complete |
| 2 | Demand generation and the ambulance trip (`duarouter`) | complete |
| 3 | SUMO run under TraCI control, with signal policies | complete |
| 4 | FastAPI + WebSocket streaming | not started |
| 5 | React + Three.js / R3F scene | not started (gated by this report) |
| 6 | Analysis, counterfactual pairing, provenance | complete |

Coordinate frames are defined in [`ARCHITECTURE.md`](ARCHITECTURE.md): WGS84
degrees in OSM, metres `(x, y)` with Y north in the SUMO network (projection
recorded in the `.net.xml` `<location>` element), and metres with **Y up** in a
Three.js scene via `x = sumo.x - ox`, `z = -(sumo.y - oy)`, `y = elevation`.

## 3. Study area

| | |
|---|---|
| Area ID | `silk_board_v1` |
| Bounding box | 77.615889, 12.910001 → 77.630635, 12.924471 |
| Area | 2.56 km² |
| Anchor | Silk Board junction, Bengaluru |
| Junction nodes shared by 3+ drivable ways | 120 |
| Major-road junction nodes | 37 |

Three corridors meet here: Hosur Road (NH-44) north–south, the Outer Ring Road
east–west, and Sarjapura Road.

## 4. OSM provenance

| | |
|---|---|
| Source | OpenStreetMap, via the Overpass API |
| Endpoint | `https://overpass.kumi.systems/api/interpreter` |
| Licence | ODbL 1.0 — © OpenStreetMap contributors |
| Retrieved | 2026-09-09T16:12:40Z |
| SHA-256 | `7531944338ea8dc55311095f42b456270b1e804b1ebf93e55a2bd2a507169804` |
| Size | 4,800,643 bytes |
| Classification | `PUBLICLY_SOURCED_DATA` |

The project's data-integrity scheme ([`DATA_INTEGRITY.md`](DATA_INTEGRITY.md))
requires every dataset to carry a class and the evidence that class demands.
`ESTIMATED_DATA` must carry its `estimation_basis`; `SIMULATED_DATA` must carry
`produced_by` and `random_seed`. **No artefact in this project is labelled
`VERIFIED_REAL_DATA`**, because nothing in it was verified against ground truth.

### Unresolved infrastructure

`way/1351994264`, tagged as an under-construction flyover, has **unresolved
operational status**. Excluding it disconnects a 1.9 km carriageway; OSM's own
`highway` tag says it is drivable. Both variants are buildable
(`include_unresolved` / `exclude_unresolved`); the committed default **includes**
it, and every result records which variant produced it. This is not a resolution
of the real-world fact — it remains unknown — and section 20 keeps it open.

## 5. SUMO conversion methodology

`netconvert` 1.27.1, from a committed configuration. Key options and why:

| Option | Why |
|---|---|
| `--proj.utm` | UTM 43N; projection recorded in the net's `<location>` |
| `--keep-edges.in-geo-boundary` | Clip to the committed study-area bbox |
| `--keep-edges.by-vclass passenger` | Motor-vehicle network only |
| `--keep-edges.components 1` | Largest weakly connected component |
| `--junctions.join`, `--junctions.join-dist 15` | OSM splits large Indian junctions into fragments |
| `--tls.guess-signals`, `--tls.guess-signals.dist 30` | All 18 OSM signal nodes have way-degree 1 — they sit on approach ways, set back from the junction |
| `--tls.join` | Cluster signal-controlled nodes |

Result: **1,515 passenger edges, 8 traffic lights**.

### Speed provenance — a load-bearing limitation

`scripts/audit_baseline.py` separates every edge speed into OSM-derived and
SUMO-estimated:

| | Count |
|---|---|
| OSM-derived speeds | **140** |
| SUMO-estimated (default) speeds | **1,375** |
| Edges defaulted to ≥ 80 km/h | **166** (12.67 km) |

netconvert's type map is derived from European conventions and assigns 100 km/h
to `highway.secondary`, which at Silk Board is an urban arterial. Time loss is
measured against free-flow, so **on those edges reported time loss is an
over-estimate**. Section 20 records where this touches the headline result.

## 6. Demand methodology

| | |
|---|---|
| Period | evening peak |
| Total flow | 2,940 vehicles/hour before scaling |
| Demand scale | **0.5** |
| Simulated window | 0 – 3,900 s (3,600 s measured + 300 s warm-up) |
| Step length | 0.5 s |
| Routing | `duarouter`, seeded, no `--ignore-errors`, no `--repair` |
| Classification | `ESTIMATED_DATA` |

The 0.5 scale is **calibrated against the model's own capacity, not chosen**: at
scale 1.0 the network gridlocked — 148 teleports and 955 vehicles that never
entered — and a gridlocked run reports travel times nobody drove. The scale is
the largest value at which teleports stay within threshold and the insertion
backlog stays small. **It is a property of this model, not of Bengaluru.**

Flow directions are distributed across the three corridors (Hosur Road 3 flows,
Outer Ring Road 4, Sarjapura Road 3). The off-peak/peak ratio and the equality of
the two peaks are stated assumptions, not derived from counts: inventing a
directional asymmetry would be inventing a fact about where Bengaluru's
employment sits.

## 7. Vehicle model

| Type | vClass | Length | Width | minGap | accel | maxSpeed | Share |
|---|---|---|---|---|---|---|---|
| `motorcycle` | motorcycle | 2.2 m | 0.8 m | 0.8 m | 3.0 | 60 km/h | **50%** |
| `car` | passenger | 4.5 m | 1.8 m | 2.0 m | 2.6 | 60 km/h | **26%** |
| `auto` | passenger | 2.6 m | 1.3 m | 1.0 m | 2.0 | 50 km/h | **14%** |
| `van` | delivery | 5.5 m | 2.0 m | 2.0 m | 2.0 | 60 km/h | 4% |
| `bus` | bus | 12.0 m | 2.5 m | 2.5 m | 1.2 | 50 km/h | 3% |
| `truck` | truck | 7.5 m | 2.4 m | 2.5 m | 1.3 | 50 km/h | 3% |

The mix is `ESTIMATED_DATA`, chosen so the simulation exercises mixed traffic
with two-wheelers dominant as is widely reported for Bengaluru. **It is not a
measured or published modal split and must not be quoted as one.** Composition
drives saturation flow, so it propagates into every queue and delay figure.

Phase 4 sensitivity: a two-wheeler-heavy mix moved mean network time loss by
−28%, a heavy-vehicle mix by +22% — while moving the **ambulance** travel time by
only −2.0 s and −3.0 s. Lane-change assertiveness moved network time loss by up
to +8% and the ambulance by ≤ 0.5 s. The ambulance result is comparatively
insensitive to these assumptions; the network-wide figures are not.

## 8. Ambulance model

The ambulance is an ordinary SUMO vehicle with an `emergency` vType: 6.0 m long,
2.2 m wide, `maxSpeed` 70 km/h. It is inserted on a real edge, routed over the
real network by `duarouter`, and queues behind traffic like anything else.

**It is observed, never driven.** Its edge, lane, position, speed and distance to
each route signal are read through TraCI every step. There is no `moveToXY` call
anywhere in the policy code, no speed factor bonus, and no `jmIgnoreFoe*`
parameter. A policy able to nudge the ambulance would make "time saved" measure
the nudge.

Origin, destination and departure time are **configuration choices by this
project, not a real EMS dispatch.** Three trips exist; see section 11.

## 9. Signal model

| | |
|---|---|
| Traffic lights | 8 |
| Control | static (fixed-time), netconvert-generated |
| Cycle length | 90 s at every signal |
| Phase counts | 3, 4 and 6 |
| Actually controlling conflicting movements | **2** |
| Classification | `ESTIMATED_DATA` / `NETCONVERT_GENERATED` |

Six of the eight are single-movement signals holding 0.889–0.911 green. Only
`joinedS_12074449284_…` (minimum movement green fraction 0.267) and
`GS_cluster_10282769895_…` (0.433) control genuinely conflicting movements.

**These are not Bengaluru's signal timings.** No observed controller timing was
available. This is the single most load-bearing assumption in the project, and
section 18 is where it does visible damage.

### How priority is granted, and why it cannot conflict

Every policy works by **selecting among the phases netconvert already
generated**; none writes a signal state string. netconvert's phases are
internally conflict-free, so any sequence of them is conflict-free too — a
structural guarantee rather than a tested property.

To reach a priority phase a policy never jumps: if the current phase already
serves the ambulance it is held; a yellow or all-red interphase is **never**
truncated; otherwise the current green ends once its minimum has elapsed and the
program advances through its own interphases. Releasing is the same in reverse.

State machine: `NORMAL → REQUESTED → PRIORITY_ACTIVE → CLEARING → NORMAL`.
`REQUESTED → NORMAL` is legal (an abandoned request); `NORMAL → PRIORITY_ACTIVE`
and `PRIORITY_ACTIVE → NORMAL` are not, and an attempt raises.

| Policy | Signals acted on | Activation | Min green | Max hold |
|---|---|---|---|---|
| `NORMAL` | none | never | — | — |
| `EMS_NEXT` | next actionable only | 250 m | 5 s | 60 s |
| `EMS_ROLLING` | all actionable in window | 700 m | 5 s | 60 s |
| `EMS_FULL_PREEMPTION` | all actionable on route | on departure | 3 s | 600 s |

`EMS_FULL_PREEMPTION` is **not a proposal**. It bounds the achievable benefit.

## 10. Counterfactual methodology

Every policy run is compared **only** against the NORMAL run of the same seed.
Before any comparison, a `ScenarioIdentity` is computed for both runs from the
network SHA-256, demand config hash, demand ID, seed, begin/end/step, vehicle
mix, ambulance origin/destination/departure, **ambulance route edges** and
scenario variant. A mismatch **raises** rather than being reported with a
caveat, because a number from a mismatched pair looks exactly like a policy
effect. The hash deliberately excludes the policy: two runs differing only in
policy hash identically, and that equality *is* the statement that they are the
same scenario.

Determinism is the assumption underneath this, and it is verified rather than
trusted (`scripts/verify_determinism.py`).

## 11. Scenario-selection methodology

Three ambulance trips exist. **All are retained and independently runnable**
(`--trip baseline|signalised|two_signal`); none has been overwritten.

| | Phase 3 `baseline` | Phase 5 `signalised` | Phase 5a `two_signal` |
|---|---|---|---|
| Origin | `312063814#2` | `312063814#2` | `1311812959#0` |
| Destination | `491889864#5` | `1411121774#1` | `1196514116#0` |
| Depart | 600 s | 600 s | 600 s |
| Route | 29 edges | 16 edges, 3,101 m | **11 edges, 2,435 m** |
| Traffic lights on route | **0** | 2 | 2 |
| Actionable | 0 | 1 | **2** |
| Green fractions | — | 1.000, 0.600 | **0.267, 0.433** |
| Stopped at a red? | n/a | **no** | **yes** |

The Phase 3 trip's route passes **no traffic lights** — the router sends it down
a residential rat-run, which is also why its waiting time was 0.0 s in all five
Phase 4 seeds. Signal priority is untestable on it. The Phase 5 trip passes two
signals but on movements green in every phase at one and least-red at the other.

The Phase 5a trip was chosen against criteria **fixed before any policy was run**
and referring only to route geometry and signal timing, never to time saved:

1. at least two actionable traffic lights on the route,
2. each with baseline green fraction < 0.70 for the ambulance's own movement,
3. the NORMAL baseline must actually stop the ambulance at a signal.

Criterion 1 originally required **three**. A per-movement audit of all eight
traffic lights showed only two have any movement below 0.70 green, so **no route
can pass three**; the count was relaxed to two and the 0.70 threshold — which
carries the mechanism — was kept. All 90 network entry/exit pairs were routed
with `duarouter` and scored; 18 reach two signals below 0.70, and this is the
shortest. The relaxation was decided and recorded before the policies were run.

## 12. Detector correction

Two defects were found in the signal-wait detector, both in measurement only.
Neither changed the simulation.

**Defect 1 — distance measured to the wrong point.** The check computed
`getDrivingDistance(ambulance, approach_edge, 0.0)` — the distance to the
*start* of the approach edge. The ambulance queues at the **stop line**, at that
edge's far end. Once on the edge, the target is behind it and TraCI returns
`INVALID_DOUBLE_VALUE` (−2³⁰), so the `0 <= d <= 60` test could essentially never
fire. Verified by direct probe:

```
halt at t = 671.0 s, edge 1393474724#1, lane position 7.6 m
  distance as previously computed:  -1073741824.0   (INVALID_DOUBLE_VALUE)
  distance to the stop line:                 8.2 m
  GS_cluster state: rrrrGy  ->  index 3, the ambulance's own link, is RED
```

**Defect 2 — halting near a signal was conflated with being stopped by one.**
The ambulance can be held on a *green* approach by the queue in front of it.
Counting that as signal delay would credit a priority policy with time it could
never recover. The detector now reads the state of the ambulance's own
controlled links at the instant of the halt and records `stopped_by_signal`.

**Consequence for Phase 5.** `COUNTERFACTUAL_REPLAY.md` concluded from an empty
`signal_wait_events` that the ambulance "never halts within 60 m of a traffic
light on its route". **That inference did not follow.** The corrected re-run is
reported in section 13.

Both fixes are confined to measurement. `observation.distance_to_tls_m`, which
the **policies** consume, was deliberately left unchanged, so policy activation
timing is identical to Phase 5 and all runs remain comparable.

## 13. Corrected-detector validation of the original Phase 5 scenario

The `signalised` scenario was re-run under the corrected detector, with the
original outputs preserved untouched (`counterfactual/seed42_*.json`) and the
corrected ones written separately (`counterfactual/signalised_seed42_*.json`).

| Quantity | Original (defective detector) | Corrected detector |
|---|---|---|
| Ambulance travel time | 205.5 s (all four policies) | **205.5 s (unchanged)** |
| Ambulance waiting | 7.5 s / 8.0 s (FULL) | **unchanged** |
| Ambulance stops | 1 | **1** |
| `signal_wait_events` | **0** in all four runs | **1** in all four runs |
| Halt classified as at-red | not recorded | **0 — the halt was on green** |
| Intersection attribution | 0.00 s saved | **0.00 s saved (unchanged)** |
| State transitions | 0 / 12 / 22 / 47 | **unchanged** |
| Signal changes | 0 / 3 / 5 / 12 | **unchanged** |
| Signal conflicts | 0 | **0** |
| Teleports | 0 / 0 / 0 / 1 | **unchanged** |

**Nothing about the simulation changed** — only what was recorded about it. Every
travel time, transition count and conflict count reproduces exactly, which is
also a determinism check.

### What the correction actually revealed

The corrected detector fires where the old one could not, and then correctly
declines to blame the signal:

```
signalised, NORMAL, seed 42
  halt at t = 713.5 s, 5.2 m from the GS_cluster stop line
  ambulance's own link 4 state: 'G'  ->  stopped_by_signal = false
  "halted near a signal that was NOT red for the ambulance's movement —
   blocked by traffic ahead, not by the signal"
```

Contrast Candidate D under the same detector:

```
two_signal, NORMAL, seed 42
  halt at t = 671.0 s, 8.2 m from the GS_cluster stop line
  ambulance's own link 3 state: 'r'  ->  stopped_by_signal = true
  "held at red on the ambulance's own movement"
```

**Phase 5's headline conclusion survives, on better evidence than the argument
that was void.** It concluded 0.00 s saved because the ambulance was never
delayed by a signal; the reason given — an empty `signal_wait_events` — did not
follow from a detector that could not fire. The corrected instrument shows the
ambulance *was* stopped near that signal, and that the signal was **green** for
its movement at the time. There was still nothing for a priority policy to
recover. The conclusion was right; its stated reason is now sound.



## 14. Multi-seed results

Five seeds (42-46), four policies each, on the Candidate D scenario. Within each
seed everything is held identical except the signal policy, and each policy is
compared only against the NORMAL run of its own seed.

### Ambulance travel time (simulated)

| Policy | 42 | 43 | 44 | 45 | 46 | mean | median | sd | min | max |
|---|---|---|---|---|---|---|---|---|---|---|
| NORMAL travel time | 184.5 | 184.0 | 184.5 | 185.0 | 184.5 | **184.5** | 184.5 | **0.354** | 184.0 | 185.0 |
| EMS_NEXT saved | 12.5 | 13.0 | 10.5 | 12.5 | 12.5 | **12.2** | 12.5 | **0.975** | 10.5 | 13.0 |
| EMS_ROLLING saved | 12.5 | 12.5 | 13.0 | 12.0 | 12.5 | **12.5** | 12.5 | **0.354** | 12.0 | 13.0 |
| EMS_FULL_PREEMPTION saved | 12.5 | 13.5 | 12.5 | 12.0 | 13.0 | **12.7** | 12.5 | **0.570** | 12.0 | 13.5 |

Paired effect size (mean paired difference over its own standard deviation):
EMS_NEXT **12.5**, EMS_ROLLING **35.4**, EMS_FULL_PREEMPTION **22.3**.
Descriptive only — five seeds cannot support a significance claim and none is
made.

### The mechanism is identical in every seed

| | 42 | 43 | 44 | 45 | 46 |
|---|---|---|---|---|---|
| NORMAL - halts near a route signal | 1 | 1 | 1 | 1 | 1 |
| NORMAL - of those, **at red on the ambulance's own movement** | **1** | **1** | **1** | **1** | **1** |
| EMS_NEXT / ROLLING / FULL - halts near a signal | 0 | 0 | 0 | 0 | 0 |

In all five seeds the baseline ambulance is stopped exactly once, at a red on its
own movement at `GS_cluster`, and **every policy removes that stop in every
seed**. The saving is not a statistical residue; it is one identifiable event
appearing and disappearing.

**The three policies are indistinguishable on ambulance benefit.** With one
actionable stop there is nothing to separate them, and the ~0.5 s spread between
their means is inside the seed-to-seed spread of each.

## 15. Safety validation

`scripts/analyse_final_experiments.py` re-checks every property against every run
in the final set, reading committed files only. **All checks pass.**

| Check | Result |
|---|---|
| Runs checked | **20** (5 seeds x 4 policies) |
| Signal conflicts | **0** |
| Signal states outside the program's phase set | **0** - validated every step, every watched signal, every run |
| Impossible simultaneous greens | **structurally excluded** - policies only select among netconvert's own phases; no state string is ever written |
| Ambulance teleports | **0** |
| Ambulance failed to complete | **0** |
| Invalid or empty routes | **0** |
| Distinct ambulance routes across seeds | **1** - identical in all 20 runs |
| Off-route edges in the ambulance's own timing | **0** |
| Policy state transitions checked | **220**, all legal under `VALID_TRANSITIONS` |
| Seeds with consistent paired scenario identity | **5 / 5** |
| Teleport tally matches SUMO's own count | yes, every run |
| Network teleports (not the ambulance) | 14 across 20 runs, each logged with vehicle, time and edge |

Grade separation: the Candidate D route uses **no** edge recorded in
`elevated_and_underground.geojson`, and does **not** touch `way/1351994264`, the
unresolved-status flyover. The route is entirely at grade.

The 14 network teleports are SUMO's deadlock escape acting on other vehicles.
They are reported rather than absorbed: a teleported vehicle's travel time is not
a time anyone drove, and each is inside the traffic aggregate of its run. None
involved the ambulance, so none affects the headline.

## 16. Uncertainty analysis

The four kinds of uncertainty are kept separate, because collapsing them is how a
simulated number turns into a claim it cannot support.

### 16.1 Simulation noise - quantified

Seed-to-seed variation with everything else fixed. For the ambulance under
Candidate D: **sd 0.354 s** on the NORMAL travel time and **sd 0.354-0.975 s** on
the saving. Phase 4 independently put the ambulance seed-to-seed sd at **1.14 s**
on a different trip. The ~12.5 s saving is roughly 13-35x the noise on the same
quantity.

For **network-wide** metrics the same noise is enormous - see 16.4 and section 18.

### 16.2 Measurement uncertainty - two defects found and fixed

Both are documented in section 12. Neither changed the simulation; both changed
what was recorded, and one had already produced a published claim that did not
follow. The current detector is validated in section 13, where it fires on a
red-stop and correctly declines to blame a green-signal halt.

Residual: the 60 m proximity radius and the 0.1 m/s halt threshold are chosen
conventions, not derived quantities.

### 16.3 Modelling assumptions - sensitivity known from Phase 4

| Assumption varied | Network mean time loss | **Ambulance** |
|---|---|---|
| Two-wheeler-heavy mix (70/15) | **-28%** | -2.0 s |
| Heavy-vehicle-heavy mix | **+22%** | -3.0 s |
| `lcAssertive` 1.0 (SUMO default) | +8% | 0.0 s |
| `lcAssertive` 4.0 | -4% | -0.5 s |
| `minGap` 2.0, `tau` 1.0 | +5% | +0.5 s |

The ambulance result is comparatively insensitive to the vehicle-behaviour and
mix assumptions. The network-wide figures are not - a second independent reason
not to report them as a policy cost.

### 16.4 Structural uncertainty - not reducible by more seeds

* **Signal programs are netconvert-generated**, uniform 90 s cycle. No observed
  controller timing was available. Section 18 is where this dominates.
* **Demand scale 0.5** is calibrated against the model's own capacity, not counts.
* **1,375 of 1,515 edge speeds are SUMO defaults**, 166 at >= 80 km/h. Tested
  directly in 16.5.
* **`way/1351994264` operational status is unresolved.** Included by default; the
  exclusion variant is buildable. The Candidate D route does not use it, so the
  headline is insensitive to the choice - the surrounding traffic is not.
* The ambulance trip is a **configuration choice**, not a real dispatch.

### 16.5 Speed-assumption sensitivity on the attributing edge

The entire 11.5 s attribution lands on one edge, `1393474724#1`, the 16 m
approach to `GS_cluster`. That edge is one of only **two** on the route whose
speed is a netconvert default rather than OSM-derived, and the default is
**100 km/h** — from a European type map, on an urban arterial approach to a
signalised junction. If the result depended on that number, it would be resting
on the weakest input in the model.

**How the sensitivity values were chosen — and why not from real-world data.**
They are drawn entirely from this project's own network. Of the 36
`highway.primary` edges in `silk_board_v1`, the 22 where OSM states a maxspeed
state **25 km/h (2 edges), 40 (4) and 60 (16)** — median and maximum 60. The 14
where OSM is silent all received the 100 km/h default, and `1393474724#1` is one
of those. Rather than pick one number, the sweep uses **the observed set of
OSM-stated values for this road class in this network**. The values were fixed
before the runs and without reference to their outcome.

> **`ASSUMED_SENSITIVITY_VALUE`.** These are not measurements, not speed limits,
> and not a claim about any road in Bengaluru. Every sensitivity artifact carries
> this class and the basis text. The primary results are unchanged and were not
> re-derived from any sensitivity run.

### Result — assumed speed on the attributing edge only

Seed 42, everything else identical. **The ambulance route is byte-identical to
the primary run in every variant**, so none of these is route-confounded.

| Assumed speed | NORMAL travel | NORMAL wait | at-red halts | approach traversal | Saved (NEXT / ROLLING / FULL) | `GS_cluster` attribution |
|---|---|---|---|---|---|---|
| **100 km/h (primary)** | 184.5 s | 5.5 s | 1 | 14.5 s | **12.5 / 12.5 / 12.5** | **11.5 s** |
| 60 km/h | 184.5 s | 5.5 s | 1 | 14.5 s | 12.5 / 12.5 / 12.5 | 11.5 s |
| 40 km/h | 188.0 s | 6.5 s | 1 | 16.0 s | 12.5 / 15.5 / 15.5 | 13.0 s |
| 25 km/h | 187.5 s | 6.5 s | 1 | 16.0 s | 14.0 / 14.5 / 14.5 | 12.5 s |

**The qualitative conclusion survives, and the saving does not shrink.** At
60 km/h the result is *identical* to the primary — the limit is not binding,
because the ambulance is queueing on that edge rather than travelling at free
flow. At 40 and 25 km/h the limit does bind, the baseline gets slower, and the
saving is the same or slightly larger (12.5 → 15.5 s). The attribution stays
concentrated at `GS_cluster` (11.5 → 13.0 s) and `joinedS_` contributes −0.0 s
throughout.

This is the expected direction: the saving is a **red wait being removed**, and a
red wait does not get shorter because the approach speed limit is lower.

### The variant that does change the answer — and what it means

A second variant lowers **both** defaulted route edges to 60 km/h. The other one,
`92196679#0`, is 964 m of the 2,435 m route and sits *upstream* of `GS_cluster`.

| | NORMAL travel | NORMAL wait | at-red halts | Saved | `GS_cluster` attribution |
|---|---|---|---|---|---|
| `bothdefaults60kmh` | **183.0 s** | **0.0 s** | **0** | **3.0 / 3.5 / 3.0** | 3.0 s |

**The baseline ambulance is never stopped at a red at all**, and the saving falls
to ~3 s. The baseline is *faster* (183.0 s) than the primary despite lower speed
limits, because it no longer waits at the signal.

The cause is not the speed limit as such. Slowing a 964 m upstream edge shifts
**when the ambulance arrives at `GS_cluster` relative to the 90 s cycle**, and it
arrives on green instead of red. This is the same mechanism that made the Phase 5
`signalised` scenario show 0.00 s (section 13): no red stop, nothing to recover.

**This does not invalidate the core finding — it bounds its generality, which is
the more important thing to state:**

* The **mechanism** is confirmed in every variant: where the baseline ambulance is
  stopped at a red on its own movement, every policy removes that stop and the
  saving is approximately the removed red wait. Where it is not stopped, there is
  little to save. The policies behave consistently in both regimes.
* The **magnitude** is a property of arrival phase, not a constant. It depends on
  when the ambulance reaches the signal within its cycle, and that is sensitive to
  upstream travel-time assumptions — assumptions that are, on this route,
  netconvert defaults.
* Therefore **"~12.5 s" is not a transferable figure.** It is what this scenario
  yields when the ambulance meets a red. A statement of the form "signal priority
  saves 12.5 s" would be false; the defensible statement is in section 19.



## 17. Intersection attribution

Per-signal attribution of the ambulance's delay change, Candidate D seed 42
(EMS_NEXT; the other policies match):

| Traffic light | Green fraction | Baseline traversal | Counterfactual | Delay reduction |
|---|---|---|---|---|
| `GS_cluster_10282769895_...` | 0.433 | 14.5 s | 3.0 s | **11.5 s** |
| `joinedS_12074449284_...` | 0.267 | 9.5 s | 9.5 s | **-0.0 s** |
| Unattributed (non-signal route edges) | - | - | - | **1.0 s** |

The attribution sums to the observed change with an explicit unattributed
residual of 1.0 s, which is **not** credited to the policy: it is traffic the
ambulance met elsewhere.

**`joinedS_` contributes nothing despite the lower green fraction.** The
ambulance passes it on green. Green fraction predicts *exposure*, not delay -
which is why the selection criteria could not on their own guarantee a testable
scenario, and why criterion 3 required an actually observed stop.

**The entire result rests on one intersection and one event.** That is a
concentration risk, and 16.5 tests the edge it depends on.

## 18. Traffic-side metric decision

**Decision: retained as a DIAGNOSTIC ONLY. It is not a cost or benefit of signal
priority and must never be reported as one.** Every comparison artifact carries
`traffic_metric_status: "DIAGNOSTIC_ONLY"` with the reason embedded in the file.

### Why - three converging results

**1. The effect reproduces with no ambulance.** Phase 5a replayed each policy's
recorded signal timeline into an ambulance-free network and reproduced
traffic-side changes of the same sign and order.

**2. It reproduces without any replay machinery either.** Phase 5b applied the
same intervention envelopes on a fixed timetable - no ambulance logic, no state
override, both arms ordinary runs. **24.5-72.0 s of signal holding moved total
time loss by tens of thousands of vehicle-seconds.**

**3. Across seeds it has no stable sign.** Phase 5b control, five seeds, change
in total time loss against the ambulance-free NORMAL arm:

| Policy | 42 | 43 | 44 | 45 | 46 | mean | sd |
|---|---|---|---|---|---|---|---|
| EMS_NEXT | -47,918 | +29,607 | +33,086 | +75,979 | +17,552 | +21,661 | **44,727** |
| EMS_ROLLING | -19,555 | +25,892 | +30,842 | +34,267 | +24,422 | +19,174 | **22,003** |
| EMS_FULL_PREEMPTION | -27,316 | +16,903 | +17,522 | +18,795 | +7,794 | +6,740 | **19,531** |

The standard deviation rivals or exceeds the mean in every case and the range
spans zero. **Seed 42's large negative - the anomaly that triggered this
investigation - was one draw from a distribution with no stable sign.** Reporting
it as a benefit of signal priority would have been reporting a seed.

### Can an ambulance-attributable component be separated?

Difference-in-differences, experiment minus fixed-schedule control, five seeds:

| Policy | DiD mean | DiD sd | DiD range |
|---|---|---|---|
| EMS_NEXT | -25,111 | 42,815 | -89,767 ... +22,467 |
| EMS_ROLLING | -26,283 | 16,737 | -54,574 ... -13,446 |
| EMS_FULL_PREEMPTION | -13,502 | 24,082 | -45,111 ... +19,687 |

**No.** Two of three intervals comfortably span zero and every standard deviation
is of the same order as its mean. At five seeds the estimator is swamped by the
perturbation response it is trying to subtract.

### Why retained rather than removed or redesigned

* **Removing it** would delete a real, reportable property of the model: its
  fixed-time signal plans are chaotically sensitive to perturbation. That is a
  finding, and a warning to anyone who would tune signals in this model.
* **Redesigning it** - the difference-in-differences above is the natural
  redesign - does not work at this seed count, and adding seeds until an interval
  excludes zero is exactly the practice this project exists to avoid.
* **Retaining it as a diagnostic** states what is true: the number measures the
  signal plan's fragility, not the policy's cost.

**A defensible traffic-side cost metric would need signal timings that are
verified rather than generated.** That is a data problem, not an analysis
problem, and it is not solvable inside this project as scoped.

## 19. Final supported findings

Stated in the only form the evidence supports. Every sentence is about a
simulation.

### SUPPORTED

1. **Under this simulation scenario (Candidate D, `two_signal`, seeds 42-46),
   each EMS signal policy reduced simulated ambulance travel time relative to its
   paired NORMAL run by a mean of 12.2 s (EMS_NEXT), 12.5 s (EMS_ROLLING) and
   12.7 s (EMS_FULL_PREEMPTION), against a paired baseline of 184.5 s, with
   seed-to-seed standard deviations of 0.975, 0.354 and 0.570 s respectively.**

2. **The saving corresponds to one identifiable, observed event.** In all five
   seeds the baseline ambulance halted exactly once at a red on its own
   controlled movement at `GS_cluster`, and in all five seeds every policy
   removed that halt. The result is a mechanism, not a residue.

3. **The policies executed without a single signal conflict** across all 20 runs
   in the final set, plus every control and sensitivity run: 0 conflicts, 0
   signal states outside the program's own phase set, 220 policy state
   transitions all legal. Conflicting greens are structurally excluded, because
   policies only select among netconvert-generated phases.

4. **The ambulance was never moved, never teleported, and always completed** in
   every run. Its route was identical across all 20 runs and across all four
   sensitivity variants.

5. **The network-wide traffic effect of these policies is not attributable to the
   ambulance.** Two independent controls reproduced it with no ambulance present,
   and across five seeds it has no stable sign (section 18).

6. **The paired counterfactual method works and is falsifiable.** It produced a
   0.00 s result on the `signalised` scenario, a ~12.5 s result on Candidate D,
   and a ~3 s result under a different upstream speed assumption — each time for
   an identifiable reason, and each time refusing to compare mismatched
   scenarios.

### CONDITIONAL — supported only under the simulation's assumptions

7. The 12.5 s magnitude holds **only when the ambulance meets a red at
   `GS_cluster`**. Whether it does is a function of arrival phase within the 90 s
   cycle, which is sensitive to upstream travel-time assumptions: under an
   alternative assumed speed for the upstream defaulted edge, the baseline
   ambulance met green and the saving fell to ~3 s (section 16.5).

8. Results are conditional on: netconvert-generated signal timings on a uniform
   90 s cycle; a demand scale calibrated to the model's own capacity; an assumed
   vehicle mix; 1,375 of 1,515 edge speeds being SUMO defaults; and
   `way/1351994264` being treated as open.

9. The three policies are **indistinguishable on ambulance benefit** in this
   scenario. That is a property of a route with one actionable stop, not a
   finding that they are equivalent in general.

### NOT SUPPORTED — claims that must not be made

10. **Any claim about real ambulances in Bengaluru.** Not travel times, not
    savings, not delay. No ambulance GPS trace, dispatch record, signal timing or
    traffic count was used. "Signal priority would save ~12 s at Silk Board" is
    **not** supported by anything in this project.

11. **That EMS signal priority improves general traffic.** The controls
    disprove the attribution outright.

12. **That EMS signal priority costs the network X vehicle-seconds.** The metric
    has no stable sign across seeds and is diagnostic only (section 18).

13. **That ~12.5 s is a transferable estimate of what priority saves.** It is
    scenario- and arrival-phase-specific (finding 7).

14. **Any significance claim.** Five seeds. Effect sizes are reported as
    descriptive ratios; no p-value, confidence interval or hypothesis test is
    claimed anywhere.

15. **That the signal programs resemble the real junction's.** They are
    generated, and no verification was possible.

### OPEN LIMITATIONS — unresolved after this R&D phase

See section 20.

## 20. Limitations

Ordered by how much they constrain the findings.

1. **Signal timings are netconvert-generated, not observed.** The most
   load-bearing assumption in the project. It sets the cycle the ambulance's
   arrival phase is measured against, and it is the direct cause of the
   traffic-side metric being uninterpretable. Not resolvable without field data.

2. **The result rests on one intersection and one event per run.** `GS_cluster`
   carries the whole attribution; `joinedS_` contributes −0.0 s despite a lower
   green fraction, because the ambulance passes it on green.

3. **Arrival phase governs the magnitude** (section 16.5). A defensible change to
   an upstream defaulted speed removed the red stop entirely and cut the saving to
   ~3 s.

4. **Speed provenance.** 1,375 of 1,515 edge speeds are SUMO defaults; 166 are
   ≥ 80 km/h from a European type map. Two of the 11 route edges are among them,
   including the one carrying the attribution. Time loss measured against
   free-flow is over-estimated on those edges.

5. **Demand, mix and vehicle behaviour are `ESTIMATED_DATA`**, not calibrated
   against counts. Phase 4 showed network metrics move ±28% under plausible mix
   variation while the ambulance moves ≤ 3 s.

6. **Five seeds.** Enough to show the ambulance result is stable and the traffic
   result is not; not enough for any distributional claim.

7. **One scenario, one departure time, one demand period.** No sweep over
   departure times, and departure time is exactly what arrival phase depends on.

8. **`way/1351994264` operational status remains unresolved.** Included by
   default. The route does not use it; surrounding traffic does.

9. **The traffic-side metric is retained only as a diagnostic** and cannot
   currently answer the project's secondary research question.

10. **Controls are not exactly equivalent to the experiment.** Both remove one
    vehicle from the demand; the Phase 5a replay control additionally perturbs by
    ~9,925 s through the replay mechanism itself, which is why Phase 5b exists.

11. **Detector conventions** — the 60 m proximity radius and 0.1 m/s halt
    threshold — are chosen, not derived.

12. **No per-step vehicle position output exists.** No FCD or netstate output is
    configured, so no committed artifact contains vehicle trajectories. This does
    not affect any finding above, but it determines what the 3D stage must build.

## 21. Reproducibility instructions

Every result in this report is produced by a committed script from committed
configuration, with the seed recorded. Nothing is hand-edited.

```bash
# 0. environment (SUMO 1.27.1 on PATH, SUMO_HOME set)
./scripts/bootstrap.sh && ./scripts/check_env.sh

# 1. network — OSM -> SUMO, deterministic from the committed .netccfg
python scripts/build_sumo_network.py
python scripts/review_sumo_network.py        # topology + operational-status review
python scripts/audit_baseline.py             # signal programs + speed provenance

# 2. determinism — the assumption every counterfactual rests on
python scripts/verify_determinism.py --seed 42 --duration 900

# 3. the final experiment: Candidate D, five seeds, four policies each
for s in 42 43 44 45 46; do
  python scripts/run_counterfactual.py --seed $s --trip two_signal
done

# 4. the controls
python scripts/run_control.py --seed 42                    # Phase 5a, replay-based
for s in 42 43 44 45 46; do
  python scripts/run_fixed_schedule_control.py --seed $s   # Phase 5b, no replay
done

# 5. aggregate, validate, quantify uncertainty (reads files only, runs nothing)
python scripts/analyse_final_experiments.py

# 6. the earlier scenarios, still reproducible
python scripts/run_counterfactual.py --seed 42 --trip signalised
python scripts/run_counterfactual.py --seed 42 --trip baseline
```

## 22. Exact experiment and configuration identifiers

| | |
|---|---|
| SUMO | 1.27.1 |
| Network file | `simulation/sumo/silk_board_v1/silk_board_v1.net.xml` |
| Network SHA-256 | `2c23d6f96c30bf220e748250fc673699…` |
| Scenario variant | `include_unresolved` (way/1351994264 included) |
| Demand ID (seed 42) | `cf_silk_board_v1_two_signal_seed42` |
| Scenario hash (seed 42) | `435e3fc2d17d4bdc` |
| Ambulance trip | `two_signal` — `1311812959#0` → `1196514116#0`, depart 600 s |
| Ambulance vehicle ID | `ambulance_two_signal` |
| Seeds | 42, 43, 44, 45, 46 |
| Window | 0 – 3,900 s, 300 s warm-up, 0.5 s step |
| Demand scale | 0.5 (2,940 veh/h base) |

### Output namespaces — experimental history is preserved

| Path | Experiment |
|---|---|
| `counterfactual/seed42_*.json` | **Original Phase 5** (`signalised`), defective detector |
| `counterfactual/signalised_seed42_*.json` | **Corrected Phase 5** (`signalised`), corrected detector |
| `counterfactual/two_signal_seed4{2..6}_*.json` | **Candidate D**, five seeds, corrected detector |
| `counterfactual/superseded/*_pre_red_green_classification.json` | Pre-classification measurements, retained |
| `control/two_signal_seed42_control.json` | **Phase 5a** replay-based control |
| `control/two_signal_seed4{2..6}_fixed_schedule_control.json` | **Phase 5b** fixed-schedule control |
| `sensitivity/sens_approach{60,40,25}kmh_*.json` | **Speed sensitivity** on `1393474724#1` (ASSUMED values) |
| `sensitivity/sens_bothdefaults60kmh_*.json` | **Speed sensitivity** on both defaulted route edges (ASSUMED) |
| `simulation/sumo/silk_board_v1/sensitivity/*.net.xml` | Patched network copies; the committed network is never modified |
| `final_analysis.json` | Aggregate safety, uncertainty and traffic analysis |
| `data/provenance/*.json` | One provenance record per dataset |

Every provenance record carries its data class, `produced_by`, `random_seed`,
tool versions and an explicit limitations list.
