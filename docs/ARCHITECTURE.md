# Architecture

How `OSM → SUMO → TraCI → FastAPI/WebSocket → React/Three.js` fits together.

None of this is implemented yet. This document is the contract the
implementation is expected to satisfy, written first so that decisions with
long-range consequences — coordinate frames, where state originates, what a
"result" is — are made deliberately rather than discovered later.

---

## 1. The governing constraint

**SUMO is the source of truth for traffic and vehicle movement.**

Everything downstream is a transport or a renderer. Data flows one way:

```
SUMO  ──►  TraCI  ──►  FastAPI  ──►  WebSocket  ──►  React/Three.js
(model)   (control)   (transport)   (transport)      (renderer)
```

Control flows the other way, but only through TraCI and only from signal
policies. The frontend can *request* a run, a pause or a policy change; it never
computes state.

The reason is evidentiary. The project's output is a claim of the form "the
ambulance lost N seconds at this junction, and this policy would have recovered
M of them". That claim is only as good as the chain from the screen back to the
model. If the browser smooths a vehicle's path because 10 Hz looks choppy, and
that smoothed path is what a viewer sees, the picture is no longer evidence of
what the model produced.

**Permitted:** interpolating between two received frames purely for display.
**Not permitted:** extrapolating beyond the latest frame, inventing vehicles,
continuing motion when frames stop arriving, or letting any of that feed back
into a reported number.

---

## 2. Stage by stage

### Stage 1 — OpenStreetMap → SUMO network

**Input:** an OSM extract for the study area — Central Silk Board Junction plus
roughly 5–10 surrounding intersections.
**Output:** `*.net.xml`, an intersection registry, provenance records.
**Label:** OSM geometry is `PUBLICLY_SOURCED_DATA`. Manual corrections are
`ESTIMATED_DATA` and are recorded individually.

The extract is downloaded from a documented source, saved untouched to
`data/raw/`, checksummed, and converted with `netconvert` driven by a committed
`.netccfg` so the conversion is repeatable.

Silk Board specifics that will need attention:

- **The elevated corridor is a real grade separation.** Flyover and surface roads
  must not be joined where they merely cross in plan view. Getting this wrong
  lets traffic change level for free and quietly invalidates every travel time.
- **Junction geometry.** OSM's representation of large Indian intersections is
  frequently split into fragments that `netconvert` will treat as several
  junctions unless joined. Review in `netedit` is expected, not optional.
- **Lane counts and turn restrictions** should be checked against imagery. Each
  correction is recorded with its reasoning.

The network is the foundation of every downstream measurement, so it gets
inspected against reality before anything is run on it.

### Stage 2 — Demand and the ambulance trip

**Input:** the network, plus traffic-demand assumptions.
**Output:** `*.rou.xml`, vehicle type definitions.
**Label:** `ESTIMATED_DATA` unless a documented count source is obtained.

Bengaluru's mixed traffic — cars, motorcycles and scooters, autos, buses, trucks,
vans — is modelled with distinct SUMO vTypes so that composition, acceleration
and gap acceptance differ realistically between them. Two-wheelers matter
disproportionately here: their lane-filtering behaviour is a large part of why
Indian junction queues behave unlike the European ones SUMO's defaults were tuned
for.

We do not have verified turning counts for Silk Board. Demand is therefore an
assumption and is labelled as one. This does not undermine the counterfactual —
both arms use identical demand — but it does bound the claim: results describe
*this modelled traffic*, and that qualifier belongs in every write-up.

The ambulance is an ordinary SUMO vehicle with an ambulance vType. It is inserted
on a real edge, routed over the network, and subject to queues and signals like
anything else. It is never moved by `moveToXY` or teleported: its travel time is
the headline number, and a number produced by nudging the vehicle is not a
measurement.

### Stage 3 — SUMO run under TraCI control

**Input:** `ScenarioConfig` — network, routes, seed, time window, policy.
**Output:** a state frame per step; SUMO output files.
**Label:** `SIMULATED_DATA`.

Per step the loop advances SUMO, reads state, offers it to the active policy
(which may issue phase changes), and emits an immutable frame.

- A **warm-up period** runs before measurement so the ambulance departs into a
  populated network rather than an empty one.
- The **seed** is fixed and recorded. Baseline and counterfactual share it, which
  is what makes their background traffic identical.
- **Teleports** (SUMO's deadlock escape) are logged. A teleport affecting a
  measured trip flags the run rather than silently improving its numbers.

### Stage 4 — FastAPI + WebSocket

**Input:** state frames.
**Output:** JSON over WebSocket; REST for control and results.

The backend holds no traffic model. It cannot compute a vehicle position, which
is deliberate: a transport layer that *could* interpolate eventually would.

- `POST /api/simulation/runs` — start a run from a config + seed
- `GET  /api/simulation/runs/{id}` — status and metadata
- `POST /api/simulation/runs/{id}/counterfactual` — replay under another policy
- `GET  /api/simulation/runs/{id}/results` — travel times, delay, ranking
- `WS   /api/simulation/runs/{id}/stream` — per-step frames

Every frame carries `step` and `sim_time_s`. Under backpressure frames are
**dropped**, never merged or averaged: a visible gap is honest, a smoothed one is
not.

### Stage 5 — React + Three.js + React Three Fiber

**Input:** state frames.
**Output:** the 3D view and the sandbox controls.

The scene is built from network geometry exported once at load, then updated
purely from incoming frames. R3F renders; it does not simulate.

**Coordinate frames** — the likeliest source of a silent, hard-to-spot error:

| Frame | Units | Notes |
|---|---|---|
| WGS84 | degrees `(lon, lat)` | What OSM stores |
| SUMO network | metres `(x, y)`, Y north | Projection recorded in `.net.xml` |
| Three.js scene | metres `(x, y, z)`, **Y up** | `x = sumo.x - ox`, `z = -(sumo.y - oy)`, `y = elevation` |

Two things to get right:

- **The Z sign flip.** SUMO is Z-up with Y pointing north; Three.js is Y-up. Omit
  the negation and the entire scene is mirrored — which reads as plausible until
  someone tries to match it against a map.
- **Re-anchoring to a scene origin.** Three.js renders in float32. UTM easting
  near Bengaluru is around 780,000 m, where float32 spacing is roughly 6 cm —
  enough for visible jitter. Subtracting a junction-local origin keeps
  coordinates small and the rendering stable.

The projection parameters come from the `<location>` element of the `.net.xml`.
They are read, never assumed: a guessed UTM zone gives positions that look
correct at the junction and are metres out at the study-area edges.

### Stage 6 — Analysis

**Input:** SUMO output from paired runs.
**Output:** delay attribution, recovered time, intersection ranking.
**Label:** `SIMULATED_DATA`, carrying both run IDs.

Detailed method in the module docstrings under `analysis/ems_analysis/`.

---

## 3. Counterfactual replay

The experimental design, and the reason the rest of the architecture is shaped
the way it is:

```
              ScenarioConfig (seed = S, config_hash = H)
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
   policy = baseline_fixed      policy = ems_preemption
            │                           │
            ▼                           ▼
      SUMO run A                   SUMO run B
            │                           │
            └─────────────┬─────────────┘
                          ▼
             compare: identical H, identical S,
                      differing policy
                          ▼
          recovered time, per-junction attribution
```

Everything except the policy is held constant. Before subtracting anything, the
analysis verifies that the two runs share a `config_hash` and a seed; if they do
not, it raises rather than returning a number. A difference between two subtly
different scenarios looks exactly like a counterfactual result once it reaches a
chart, and by then the discrepancy is invisible.

A single seed gives one traffic realisation. Claims about ranking need repeated
seeded pairs and a reported spread.

---

## 4. Sandbox

The interactive layer, once the pipeline works: choose ambulance origin and
destination, dispatch time, demand level and policy; run baseline and
counterfactual; see the difference on the 3D view and in the ranking.

Every sandbox run is a real SUMO run with a saved config and seed. The sandbox
never previews a result it has not simulated — an estimate rendered in the same
UI as a measurement will be read as a measurement.

---

## 5. Build order

Working simulation before visual polish; see `docs/ROADMAP.md`. The dependency is
real rather than a preference: the state schema, the coordinate transform and the
scene geometry all follow from what the network and the TraCI loop actually
produce. Building the 3D view first would mean designing it against imagined data
and then discovering the real data has a different shape.
