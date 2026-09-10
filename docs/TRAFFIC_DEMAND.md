# Phase 3: baseline traffic demand

The demand model for the Silk Board baseline — vehicle types, flow rates,
routing, and the provenance of every number in it.

> **Nothing in this document is a measurement of Bengaluru traffic.** No traffic
> count, modal split or signal timing for Silk Board was available to this
> project. The demand is an assumption, labelled `ESTIMATED_DATA`; the traffic it
> produces is `SIMULATED_DATA`. Absolute travel times from this model describe
> *this modelled traffic*, and exist to be subtracted from a counterfactual
> rather than quoted as congestion figures.

Machine-readable: [`data/processed/silk_board_v1/demand_report.json`](../data/processed/silk_board_v1/demand_report.json)

---

## 1. Vehicle types

Seven types, in `ems_sim.demand.vehicle_types`. **Every parameter is
`ESTIMATED_DATA`** and carries a written basis; none was measured at Silk Board
or taken from a published Bengaluru study.

| Type | vClass | Length | Width | minGap | accel | maxSpeed |
|---|---|---|---|---|---|---|
| `motorcycle` | motorcycle | 2.2 m | 0.8 m | 0.8 m | 3.0 | 60 km/h |
| `auto` | passenger | 2.6 m | 1.3 m | 1.0 m | 2.0 | 50 km/h |
| `car` | passenger | 4.5 m | 1.8 m | 2.0 m | 2.6 | 60 km/h |
| `van` | delivery | 5.5 m | 2.0 m | 2.0 m | 2.0 | 60 km/h |
| `ambulance` | emergency | 6.0 m | 2.2 m | 2.0 m | 2.4 | 70 km/h |
| `truck` | truck | 7.5 m | 2.4 m | 2.5 m | 1.3 | 50 km/h |
| `bus` | bus | 12.0 m | 2.5 m | 2.5 m | 1.2 | 50 km/h |

Two categories of parameter, distinguished because they carry different risk:

- **Physical dimensions** are typical values for the class. A wrong figure is
  wrong by tens of percent.
- **Behavioural parameters** are SUMO defaults, except where noted. The
  departures matter more: `motorcycle.lcAssertive = 2.0` (against a default of 1)
  and `latAlignment = arbitrary` encode **lane-filtering**, the single largest way
  Indian junction behaviour differs from the European traffic SUMO's defaults were
  calibrated on. It is uncalibrated, and it drives saturation flow — how many
  vehicles a green phase discharges — which drives queue length, which is what
  this project reports.

### The ambulance has no priority in this phase

`speedFactor = 1.0`, no junction-model overrides, no siren, no preemption.
`vClass=emergency` alone grants nothing in SUMO.

This is deliberate. Phase 3 is the baseline that Phase 5's counterfactual is
subtracted from. A baseline ambulance that already carried some priority would
make the recovered time reported later **too small** — the comparison would be
against a head start. A test asserts no priority parameter is set.

---

## 2. Demand structure

The network is a 1.6 km box cut out of Bengaluru, so traffic enters and leaves at
the boundary. Demand is expressed as **flows between boundary source edges** (no
incoming edge inside the box) and **boundary sink edges** (no outgoing edge).

**8 sources, 8 sinks, 14 OD flows.** All four flyover terminals identified in
Phase 2.5 are included: `886153772` and `684917326#0` as sources, `886153773` and
`172853384#3` as sinks.

| Corridor | Flows |
|---|---|
| Hosur Road (NH-44), north–south | 3 |
| Outer Ring Road, east–west | 4 |
| Sarjapura Road | 3 |
| Ragigudda elevated corridor | 2 |
| Madiwala side roads | 2 |

### Vehicle mix

| Type | Share |
|---|---|
| motorcycle | 50% |
| car | 26% |
| auto | 14% |
| van | 4% |
| bus | 3% |
| truck | 3% |

`ESTIMATED_DATA`, and the most conspicuous assumption here. Two-wheelers dominate
because that is the widely observed character of Bengaluru traffic, but **this
split is not taken from any survey or published modal share and must not be
quoted as one.**

### Time of day

| Period | Scaling |
|---|---|
| `morning_peak` | 1.00 |
| `off_peak` | 0.45 |
| `evening_peak` | 1.00 (default) |

`ESTIMATED_DATA`. The two peaks are equal because this project has no basis for
making one heavier: modelling a directional asymmetry would mean asserting where
Bengaluru's employment sits relative to Silk Board, which is a fact about the
city that nobody here has established.

---

## 3. Known limitations of the demand model

1. **The rates are not counts.** They are assumptions calibrated against the
   model's capacity (§4).
2. **Boundary-to-boundary only.** Trips beginning or ending inside the study area
   are not modelled, so the mix is skewed toward through-traffic. Real junction
   traffic includes both.
3. **No directional asymmetry** between morning and evening.
4. **Signal programs are netconvert defaults** (`ESTIMATED_DATA`, Phase 2.5 §10),
   so the junction's discharge behaviour is assumed, not observed.
5. **Free-flow speeds are SUMO-supplied on 1,375 of 1,515 edges**, 166 of them at
   ≥80 km/h. Time loss is measured against free-flow speed, so reported time loss
   is an over-estimate on those edges. See §5.

---

## 4. Calibration — how the flow rates were set

The base rates were set by judgement, and judgement was wrong. At scale 1.0
(5,880 veh/h) the full run **gridlocked**:

- **148 teleports** (124 jam, 15 yield, 9 wrong-lane)
- **955 of 6,407 vehicles never entered the network**
- teleports rising monotonically with time — 3 per 500 s early, 36 per 500 s late

A teleport means SUMO gave up on a stuck vehicle and moved it. Its recorded
travel time is then not the time to drive that path, so a run with 148 of them
reports numbers that are not measurements of the journeys they claim.

The demand's own stated basis is *congested but moving*. A global `demand_scale`
was introduced and swept to find the largest value that meets it. Sweep at 1800 s
of evening peak, seed 20260910:

| Scale | veh/h | Loaded | Departed | Arrived | Backlog | **Teleports** | Mean time loss |
|---|---|---|---|---|---|---|---|
| 0.35 | 2,058 | 1,250 | 1,250 | 1,121 | 0 | **0** | 24.8 s |
| **0.50** | **2,940** | **1,760** | **1,760** | **1,515** | **0** | **0** | **60.2 s** |
| 0.65 | 3,822 | 2,272 | 2,271 | 1,769 | 0 | **13** | — |
| 1.00 | 5,880 | 6,407 | 5,451 | 4,117 | 955 | **148** | 235.4 s |

`DEFAULT_DEMAND_SCALE = 0.5`. 0.35 was rejected as nearly free-flowing — a
junction study needs queues. 0.65 exceeds the teleport threshold of 10.

Reproduce: `python scripts/calibrate_demand.py --scales 0.35 0.5 0.65 --duration 1800`

### What the calibrated scale does and does not mean

It is **a property of this model, not of Bengaluru.** The volume this network
carries is bounded by things the model assumed rather than observed — netconvert's
default signal programs, and lane counts SUMO supplied on 1,227 of 1,515 edges.
A better-sourced network would carry a different volume.

So: **do not read 2,940 veh/h as an estimate of Silk Board's throughput.** It is
the demand at which this model stays measurable.

---

## 5. Lane and speed provenance, exposed to the demand layer

Phase 3 **replaces no default**. It surfaces Phase 2.5's inventory so that no
figure computed on this network is quoted without the caveat.

| | Lane count | Speed |
|---|---|---|
| **OSM-derived** (`PUBLICLY_SOURCED_DATA`) | 288 edges | 140 edges |
| **SUMO-estimated** (`ESTIMATED_DATA`) | 1,227 edges | 1,375 edges |

Full per-edge listing in `demand_report.json` under `lane_and_speed_provenance`,
including every affected edge ID so a later phase can filter on it.

**Why this matters for Phase 3 specifically:** time loss is measured against
free-flow speed, and SUMO supplied free-flow speed on 1,375 of 1,515 edges — 166
of them at ≥80 km/h, from a type map derived from European conventions. **Time
loss reported by this baseline is therefore an over-estimate on those edges.**

Not corrected here: no source in this project supports a better value, and
substituting a guess would be worse than a labelled default.

The word *real* is never used for a SUMO-supplied value. A test asserts it.

---

## 6. Reproducing

```bash
python scripts/run_baseline.py --dry-run          # show the plan
python scripts/run_baseline.py --demand-only      # generate + validate routes
python scripts/run_baseline.py                    # full baseline
python scripts/run_baseline.py --period off_peak
python scripts/calibrate_demand.py --scales 0.4 0.5 0.6
```

Determinism: the same `DemandConfig` writes byte-identical flow definitions
(asserted by a test that hashes them) and passes the same seed to duarouter and
to SUMO. The `config_hash` covers flows, mix, seed and timing — two runs sharing
it share a demand set, which is the precondition for Phase 5 comparing them.

### Files

| Path | Contents | Committed? |
|---|---|---|
| `simulation/demand/*.flows.xml` | Flow definitions + vTypes + the ambulance trip | No — regenerable |
| `simulation/routes/*.rou.xml` | duarouter output, one route per vehicle | No — regenerable |
| `simulation/config/*.sumocfg` | The scenario configuration | No — regenerable |
| `simulation/results/<demand_id>/` | tripinfo, summary, statistics, queues, vehroutes | No |
| `data/processed/silk_board_v1/demand_report.json` | Demand + provenance | **Yes** |
| `data/provenance/phase3_baseline.json` | Run provenance | **Yes** |

Routing uses duarouter **without** `--ignore-errors` or `--repair`. An OD pair
that cannot be connected is a finding about the network; a repaired route is a
different journey from the one the configuration asked for.
