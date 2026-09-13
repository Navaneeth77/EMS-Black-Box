# Roadmap

Build order. The sequencing is a dependency chain, not a preference: each phase
produces the data the next one needs, and the application stays runnable
throughout.

Working simulation comes before visual polish because the state schema, the
coordinate transform and the scene geometry all follow from what the network and
the TraCI loop actually produce. A 3D view built first would be designed against
imagined data.

---

### Phase 0 — Scaffold ✅

Repository structure, environment contracts, health endpoint, tests.

**Done when:** backend serves `/api/health`, frontend renders and reports backend
reachability, tests pass. No simulation, no data.

---

### Phase 1 — Study area and network ✅ *(acquisition; human review outstanding)*

**Done — acquisition and validation.** Study area defined and anchored to OSM
geometry; extract downloaded, validated (33 checks, 0 errors) and written to
`data/processed/silk_board_v1/` with two chained provenance records. Full
report: [`docs/STUDY_AREA.md`](../STUDY_AREA.md).

```bash
python scripts/ingest_study_area.py
python scripts/plot_study_area.py
```

**Remaining — human review, then conversion.** No automated check can confirm
road geometry matches reality, so the network must be inspected against imagery
before anything is built on it. The specific items are listed under "Before
Phase 2" in `docs/STUDY_AREA.md`: flyover/ramp connectivity, junction
fragmentation, the `(u/c)` flyover's status, lane counts on the approaches, and
turn-restriction completeness.

Then convert with `netconvert` via a committed `.netccfg`.

**Done when:** the network loads in `netedit`, has been visually checked against
the real junction, and every correction is recorded as `ESTIMATED_DATA` with its
reasoning.

**Deliberate checkpoint:** review the network before building anything on it.
Every downstream number inherits its errors, and they get much harder to spot
once results exist.

---

### Phase 2 — OSM → SUMO conversion ✅

Network converted with `netconvert` from a committed `.netccfg` and structurally
validated: 1,515 edges, 664 junctions, 12 traffic lights, 1 connected component,
and the flyover verified grade-separated (8 plan-view crossings, 0 sharing a
junction). Full report: [`docs/SUMO_CONVERSION.md`](../SUMO_CONVERSION.md).

```bash
python scripts/build_sumo_network.py
python scripts/plot_sumo_network.py
```

**Key finding:** grade separation in SUMO is *topological*, not vertical — it
comes from crossing ways sharing no node. `--osm.layer-elevation` was disabled
after measurement because it introduced gradients up to 1047% on short
layer-transition connectors, which would corrupt SUMO's grade resistance and so
the travel times this project reports.

**Outstanding:** inspection in `netedit` — one 580 m segment of Sarjapura Road
was pruned as disconnected, and the sharp turns and junction joins need a look.
See §11 of the conversion doc.

### Phase 2.5 — Network review and correction ✅

Six open Phase 2 items investigated; report:
[`docs/SUMO_NETWORK_REVIEW.md`](../SUMO_NETWORK_REVIEW.md).

```bash
python scripts/build_sumo_network.py    # rebuild, revalidate, regenerate the review
python scripts/review_sumo_network.py   # render it for a person
```

**Corrected:** `--tls.join-dist` raised from 20 m to 60 m. The Sarjapura Road
junction had come out as five independent traffic lights with pairs 10.3 m
apart, which would have made a Phase 5 priority policy actuate five objects the
real junction controls as one. Traffic lights 12 → 8; edges, junctions and grade
separation unchanged.

**Resolved:** the Sarjapura Road fragment is a study-area clipping artefact, not
an OSM gap (its connection points are 8 m from the northern boundary). Ramp and
deck connectivity is sound — 0 decks connect to a road they cross over. No
junction merge collapsed a grade separation.

**Still open, needs a human:** whether `way/1351994264` (the "(u/c)" flyover) is
open to traffic — it is the sole connection to a 1.9 km carriageway, and OSM
contradicts itself; whether the four traffic lights at Silk Board should be one;
and the free-flow speed for 166 edges defaulted to ≥80 km/h.

### Phase 3 — Baseline demand and simulation ✅

Seven vehicle types, a seeded boundary-to-boundary demand model, deterministic
routing, and a TraCI baseline run. Reports:
[`docs/TRAFFIC_DEMAND.md`](../TRAFFIC_DEMAND.md) and
[`docs/BASELINE_SIMULATION.md`](../BASELINE_SIMULATION.md).

```bash
python scripts/run_baseline.py
python scripts/calibrate_demand.py --scales 0.35 0.5 0.65
```

**Result:** 3,226 vehicles, 2,937 completed, **0 teleports**, 0 collisions, 0
insertion backlog. Ambulance completed its 29-edge route in 148.5 s with no
priority of any kind. 18 checks, 0 errors.

**The teleport guard did its job.** The first full run at the initially chosen
flow rates produced 148 teleports and left 955 vehicles unable to enter, and was
rejected — a teleported vehicle's travel time is not the time to drive its path.
That forced a calibration sweep, and `demand_scale` was set to the largest value
that keeps the network congested but moving.

**Everything here is assumed.** Demand rates, vehicle parameters and the
ambulance trip are `ESTIMATED_DATA`; the results are `SIMULATED_DATA`. Neither is
a measurement of Bengaluru traffic.

### Phase 4 — Baseline calibration and uncertainty ✅

Multi-seed baseline, determinism verification, sensitivity experiments and
parameter audits. Reports:
[`docs/BASELINE_CALIBRATION.md`](../BASELINE_CALIBRATION.md) and
[`docs/BASELINE_UNCERTAINTY.md`](../BASELINE_UNCERTAINTY.md).

```bash
python scripts/run_multi_seed_baseline.py    # seeds 42-46
python scripts/verify_determinism.py
python scripts/run_sensitivity.py --experiment both
python scripts/audit_baseline.py
```

**The number Phase 5 needs:** across 5 seeds the ambulance travel time is
145.90 s mean, sd **1.14 s**, range 144–147 s, on an identical 29-edge route in
every run. That is the noise floor — a recovered time below ~3 s would be
indistinguishable from a different random draw.

**Findings.** Vehicle mix is the most sensitive assumption (32.6 s swing in mean
time loss); `lcAssertive` is far less consequential than Phase 3 feared (4.94 s,
below seed noise); and `latAlignment` is **inert** — SUMO's sublane model is not
enabled, so half the two-wheeler lane-filtering model does nothing. Only **2 of
8 traffic lights** actually control conflicting movements. The worst simulated
congestion is not at Silk Board but on a minor-road rat-run to the south-east.

**Nothing was tuned.** No observation of Silk Board exists in this project to
tune towards, and fitting to expectation would make the model agree with its
author rather than with the world.

### Phase 4b — Demand and vehicle types *(superseded — folded into Phase 3)*

SUMO vTypes for Bengaluru's mixed traffic: cars, motorcycles/scooters, autos,
buses, trucks, vans, ambulance. Demand as `ESTIMATED_DATA` with recorded
reasoning.

**Done when:** SUMO runs headlessly to completion with plausible queues at
signalised approaches, and the demand assumptions are documented.

---

### Phase 4 — Signal control interface

The step loop, state frames, and the ambulance as a real routed SUMO vehicle.

**Done when:** the same config and seed produce byte-identical output across
runs, and the ambulance's travel time is read from SUMO output.

---

### Phase 5 — EMS signal priority and counterfactual replay 🔄 *(current)*

Four policies (NORMAL, EMS_NEXT, EMS_ROLLING, EMS_FULL_PREEMPTION), paired
scenario verification, and per-intersection attribution. Reports:
[`EMS_SIGNAL_POLICIES.md`](../EMS_SIGNAL_POLICIES.md),
[`COUNTERFACTUAL_REPLAY.md`](../COUNTERFACTUAL_REPLAY.md),
[`EMS_DELAY_ATTRIBUTION.md`](../EMS_DELAY_ATTRIBUTION.md).

```bash
python scripts/run_counterfactual.py --seed 42
```

**Seed 42 only. Seeds 43-46 have not been run**, pending review.

**Result: all four policies gave the same simulated ambulance travel time,
205.5 s — 0.00 s saved — at a traffic cost of 17,270 to 33,575 vehicle-seconds.**
The ambulance is never stopped by a signal on its route: it arrives on green at
the one actionable traffic light and spends 3.5 s on its approach. A priority
policy can only recover time a signal was taking, and here it was taking none.

That is the counterfactual method doing its job — distinguishing "the
intervention helped" from "the intervention ran". The policies demonstrably ran
(12/22/47 state transitions, 3/5/12 signal changes, 0 conflicts).

**Found on the way:** the Phase 3 ambulance route passes **zero** traffic lights,
which is why its waiting time was 0.0 s in every Phase 4 seed. Phase 5 selected a
destination whose route passes signals; the Phase 3 trip is unchanged so Phase 4's
numbers still stand.

### Phase 5a — scenario reselection, detector fix, and control ✅ seed 42 only

Full account: [`PHASE5A_CONTROL.md`](PHASE5A_CONTROL.md).

A new trip (`two_signal`, `1311812959#0` → `1196514116#0`) was selected against
criteria fixed before any policy was run: two actionable signals with green
fraction below 0.70, and a NORMAL baseline that actually stops the ambulance. The
Phase 3 and Phase 5 trips are untouched and still runnable via `--trip`.

**A measurement defect was found and fixed.** `signal_wait_events` measured
distance to the start of the approach edge rather than the stop line, so it could
essentially never fire. Phase 5's claim that the ambulance "never halts within
60 m of a traffic light" does not follow from it.

**Result: 12.5 s saved (184.5 s → 172.0 s), identical under all three EMS
policies, 0 signal conflicts, 0 teleports.** 11.5 s attributed to `GS_cluster`.

**But the traffic side came out negative** — every policy *reduced* network-wide
time loss. A control replaying each policy's recorded signal timeline into an
**ambulance-free** network reproduced the effect (−23,559 s, −5,161 s,
−40,182 s), so it is the signal perturbation, not the ambulance. It is most
likely an artefact of perturbing netconvert's unoptimised fixed-time plans.

**Do not claim EMS priority improves general traffic.** The traffic-side metric
in this model is currently uninterpretable as a policy cost.

**Still seed 42 only. Seeds 43-46 have not been run.**

### Phase 5b — control, multi-seed and the R&D gate ✅ seeds 42-46

Full account: [`FINAL_RND_REPORT.md`](FINAL_RND_REPORT.md) ·
gate: [`3D_READINESS.md`](3D_READINESS.md)

Five seeds on Candidate D. **Under this simulation scenario the policies reduced
simulated ambulance travel time by a mean of 12.2-12.7 s against a paired 184.5 s
baseline** (sd 0.354-0.975 s), by removing one at-red halt that occurred in the
baseline of every seed. 0 signal conflicts, 0 ambulance teleports, 220 legal
transitions, 5/5 consistent paired identity.

A **fixed-schedule control** (no ambulance logic, no replay override) showed the
traffic-side term has **no stable sign across seeds** — it ranges from -47,918 to
+75,979 s with no ambulance in the network. The metric is now
**`DIAGNOSTIC_ONLY`** and must never be reported as a cost or benefit of
priority.

A **speed sensitivity** on the edge carrying the attribution confirmed the
finding survives at every OSM-stated speed for that road class (12.5-15.5 s
saved), but showed the magnitude depends on **arrival phase**: under a different
upstream speed assumption the ambulance met green and the saving fell to ~3 s.

**Gate: READY FOR 3D.**

### Phase 5c — deferred

Fixed-time baseline; EMS preemption with realistic detection range and clearance
time. Paired runs with `config_hash` and seed verification.

**Done when:** a baseline/counterfactual pair runs from saved configs and the
comparison refuses mismatched scenarios.

---

### Phase 6 — Delay attribution and ranking

Per-edge and per-junction attribution; recovered time; ranking across repeated
seeds, with cross-traffic cost reported alongside.

**Done when:** the attribution sums to observed total delay with an explicit
unattributed residual, and rankings are stable across seeds — or their
instability is reported.

---

### Phase 7 — Streaming and 3D visualisation

WebSocket streaming; R3F scene from real network geometry; vehicles driven by
received frames only.

**Done when:** a vehicle's on-screen position matches its SUMO position at the
same simulation step, checked directly rather than assumed.

---

### Phase 8 — Sandbox

Interactive origin/destination, dispatch time, demand level, policy. Every run is
a real SUMO run with a saved config and seed.

---

### Later

PostGIS persistence for runs and results; batch seed sweeps; additional junctions
beyond the initial study area.
