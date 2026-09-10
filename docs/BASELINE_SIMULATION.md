# Phase 3: baseline simulation

The reproducible baseline run for the Silk Board network — how it is executed,
what it measures, and what its numbers mean.

**Status:** run complete. **18 checks, 0 errors, 0 warnings, 0 teleports.**

> The results are `SIMULATED_DATA` produced from `ESTIMATED_DATA` demand. They
> are a baseline for Phase 5's counterfactual to be subtracted from — **not
> measurements of Bengaluru traffic, and not measurements of any real EMS
> journey.**

Machine-readable: [`data/processed/silk_board_v1/baseline_validation.json`](../data/processed/silk_board_v1/baseline_validation.json)
· Provenance: [`data/provenance/phase3_baseline.json`](../data/provenance/phase3_baseline.json)
· Demand: [`docs/TRAFFIC_DEMAND.md`](TRAFFIC_DEMAND.md)

---

## 1. Results

```bash
python scripts/run_baseline.py
```

| | |
|---|---|
| Demand | `silk_board_v1_evening_peak_seed20260910_s0p5` |
| Period / seed / scale | weekday evening peak · 20260910 · 0.5 (calibrated) |
| Window | 0–3900 s (300 s warm-up + 3600 s measured), 0.5 s steps |
| **Wall clock** | **157.9 s** (24.6× real time), 7,800 steps |
| Vehicles loaded / departed | 3,226 / 3,226 |
| Arrived | 2,937 |
| Still running at end | 289 |
| **Insertion backlog** | **0** |
| **Teleports** | **0** (SUMO's own tally agrees) |
| Collisions | 0 |

Vehicles by type: 1,599 motorcycle · 837 car · 452 auto · 133 van · 102 bus ·
102 truck · 1 ambulance.

**289 vehicles still running at the end is expected**, not a fault: demand runs
to the end of the window, so the last departures cannot finish inside it.

### All vehicles (SUMO trip statistics, 2,937 completed)

| Metric | Value |
|---|---|
| Mean route length | 2,453 m |
| Mean speed | 11.44 m/s (41 km/h) |
| Mean duration | 267.0 s |
| Mean waiting time | 59.4 s |
| Mean time loss | 91.4 s |
| Mean depart delay | 0.49 s |

### The ambulance

`ambulance_baseline`, Hosur Road south → Outer Ring Road west, departing 600 s,
**29 route edges through the Silk Board interchange**.

| Metric | Value |
|---|---|
| **Travel time** | **148.5 s** |
| Waiting time | 0.5 s |
| Time loss | 20.2 s |
| Route length | 2,134 m |
| Stops | 1 |
| Teleported | No |

`SIMULATED_DATA`. A simulated ambulance through simulated traffic **with no
priority of any kind** — no siren, no preemption, no speed bonus. That is the
point: this is the number Phase 5 subtracts its counterfactual from.

**Do not read 148.5 s as a Silk Board ambulance travel time.** It is the travel
time of one configured trip through one assumed traffic model.

---

## 2. How the run works

```
DemandConfig ──► flows.xml ──duarouter──► routes.rou.xml
                                                │
                                        route validation  ── fails here, nothing simulated
                                                │
                                        sumocfg + TraCI loop
                                                │
                                      measurements + validation
                                                │
                    demand_report · baseline_validation · phase3_baseline
```

The TraCI loop advances SUMO one step at a time and **reads** state after each
step. No policy actuates anything in this phase — there is nothing to actuate
yet — but the loop is shaped for the signal policy Phase 5 hangs off it.

**Binaries are resolved by absolute path.** The `sumo` on this machine's PATH is
a symlink to the GUI launcher, which starts sumo-gui in the background and
returns immediately: a headless run through it simulates nothing.

### A trap worth recording

SUMO accepts exactly **one** TraCI client. An earlier version started SUMO as a
subprocess and then probed the port with a socket to test readiness — the probe
looked like the client connecting and disconnecting, so SUMO shut down, and the
real client then failed with *"Could not connect in 21 tries"*, which points
nowhere near the cause. `traci.start()` launches and connects atomically.
`sumo_process.start_sumo` carries the warning.

---

## 3. What is measured

Everything is read from SUMO. Per-trip figures come from SUMO's `tripinfo`
output and totals from its `statistics` output — **not reconstructed in the
loop**, because reconstruction was wrong: `getLoadedNumber()` is a per-step
figure and missed vehicles loaded before the first step, producing a loaded count
*below* the departed count.

| Recorded | Source |
|---|---|
| Simulation time, step count | TraCI |
| Travel time, waiting time, time loss | tripinfo |
| Route, route length, stop count | tripinfo + TraCI |
| Speed (max, sampled mean) | TraCI |
| Queues | `queue-output` |
| Vehicle routes as driven | `vehroute-output` |
| Teleports | TraCI event lists, cross-checked against SUMO's tally |
| Per-10 s samples | TraCI (running/halting counts, mean speed, ambulance state) |

### Nothing missing is filled in

A vehicle that never arrived has **no travel time**, and the record says so
rather than substituting the simulation end time. A substituted value would look
like a measurement and would not be one. Tests assert this for both the
"departed but did not arrive" and "never departed" cases.

---

## 4. Teleports

A teleport means SUMO gave up on a vehicle that had been stuck longer than
`--time-to-teleport` (300 s) and moved it. Its recorded travel time is then **not
the time to drive that path**.

**Teleporting is not disabled.** Switching it off would not remove the gridlock,
it would hide it — vehicles jammed forever and trips that never complete.

Every teleport is recorded with vehicle ID, simulation time, edge, lane and
reason, and the count is cross-checked against SUMO's own tally: a mismatch means
the loop missed some, and an unobserved teleport cannot be attributed to a trip.

**The run fails validation above a configurable threshold (default 10).** This
is not theoretical — it rejected the first full run at 148 teleports, which is
what forced the demand calibration in `TRAFFIC_DEMAND.md` §4. The current run has
**0**.

---

## 5. Network boundary

The network is a 1.6 km box, so traffic enters and leaves at its edges: **8
boundary sources** (no incoming edge inside the box) and **8 boundary sinks** (no
outgoing edge). Validation asserts that every route starts at a documented source
and ends at a documented sink — no vehicle is created or destroyed anywhere else.

All four Phase 2.5 flyover terminals are exercised: `886153772` and
`684917326#0` as sources, `886153773` and `172853384#3` as sinks.

Capacity was checked against each terminal's own lane count. The tightest is
`491889864#5` (Outer Ring Road west sink) at **1 lane** — OSM-sourced, not a
default — while the corridors feeding it are 3 lanes. At scale 1.0 it was the
first thing to back up. At the calibrated scale it runs at roughly 0.4 of a
1,800 veh/h/lane saturation flow.

---

## 6. Route validation — before anything is simulated

Routes are validated **before** the simulation, because a broken route does not
stop SUMO: it produces a run that completes and reports travel times that are not
the times to drive those journeys.

| Check | Result |
|---|---|
| Routes present | 3,226 vehicles over 165 distinct edges |
| **No broken route step** | **0** — every step follows an existing connection |
| **No illegal grade transition** | **0** — no route changes level except at a ramp or touchdown |
| All departures inside the demand window | 0 outside |
| Traffic enters at boundary sources | all |
| Traffic leaves at boundary sinks | all |
| Ambulance has a route | 29 edges |

Nothing is repaired. `duarouter` runs **without** `--ignore-errors` and
`--repair`: an unroutable OD pair is a finding about the network, and a repaired
route is a different journey from the one the configuration asked for.

### Unresolved infrastructure — 192 vehicles

**192 vehicles route over `way/1351994264`**, the "(u/c)" flyover whose
operational status Phase 2.5 could not establish. Their travel times are
**conditional on a fact nobody has verified.**

It is left enabled rather than excluded, because excluding it would isolate a
1.9 km carriageway (Phase 2.5 §1). The usage is counted on every run so the
condition travels with the result.

---

## 7. Reproducibility

Every output file carries a `reproducibility` block:

| Field | Value |
|---|---|
| SUMO version | 1.27.1 |
| Network SHA-256 | recorded per run |
| Demand config hash | `dcf9b62dc428549a` |
| Seed | 20260910 |
| Step length | 0.5 s |
| Window / warm-up | 0–3900 s / 300 s |
| Ambulance trip | origin, destination, departure |
| Timestamp | ISO-8601 UTC |

The same `DemandConfig` writes byte-identical flow definitions — asserted by a
test that hashes them — and passes the same seed to duarouter and SUMO. Two runs
sharing a `config_hash` share a demand set, which is the precondition for Phase 5
comparing them.

---

## 8. Validation checks

**18 checks, 0 errors, 0 warnings.**

| Check | Severity | Result |
|---|---|---|
| `routes_present` / `routes_are_connected` | ERROR | PASS |
| `no_illegal_grade_transitions` | ERROR | PASS |
| `traffic_enters_at_boundary_sources` | ERROR | PASS |
| `traffic_leaves_at_boundary_sinks` | ERROR | PASS |
| `ambulance_has_a_route` | ERROR | PASS |
| `simulation_terminated_correctly` | ERROR | PASS — 3900 s over 7,800 steps |
| `vehicles_departed` | ERROR | PASS — 3,226 |
| **`teleports_within_threshold`** | ERROR | **PASS — 0** |
| `ambulance_departed` | ERROR | PASS |
| **`ambulance_completed_its_route`** | ERROR | **PASS — 148.5 s** |
| `ambulance_was_not_teleported` | ERROR | PASS |
| `background_trips_completed` | ERROR | PASS — 2,937 |
| `insertion_backlog_is_small` | WARNING | PASS — 0 |
| `teleport_observations_match_sumo` | WARNING | PASS |
| `no_collisions` | WARNING | PASS |
| `departures_within_demand_window` | WARNING | PASS |
| `unresolved_infrastructure_usage_recorded` | WARNING | PASS — 192 counted |

---

## 9. What these numbers are not

1. **Not Bengaluru traffic.** Demand is assumed. Absolute travel times describe
   this modelled traffic.
2. **Not a real EMS journey.** The ambulance trip is a configuration choice.
3. **Not calibrated vehicle behaviour.** Two-wheeler lane-filtering in
   particular is an uncalibrated `lcAssertive` value that drives saturation flow.
4. **Time loss is over-estimated** where SUMO supplied free-flow speed — 1,375 of
   1,515 edges, 166 at ≥80 km/h.
5. **Signal timings are netconvert defaults** (`ESTIMATED_DATA`, Phase 2.5).
6. **192 vehicles' times are conditional** on unresolved infrastructure.
7. **Not a counterfactual.** Nothing here says what a different signal policy
   would do — that is Phase 5, and it needs this baseline to subtract from.

---

## 10. Unresolved, carried into Phase 4/5

| # | Item | Effect |
|---|---|---|
| 1 | `way/1351994264` operational status | 192 vehicles' travel times are conditional |
| 2 | Free-flow speeds SUMO-supplied on 1,375 edges | Reported time loss is an over-estimate |
| 3 | Signal programs are netconvert defaults | Junction discharge is assumed |
| 4 | Silk Board is 4 independent traffic lights | Phase 5 preemption must coordinate all four |
| 5 | Demand not validated against any count | The calibrated scale is a model property |
| 6 | Vehicle behaviour uncalibrated | Saturation flow is assumed |
