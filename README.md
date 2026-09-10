# EMS Black Box

A research-grade, **software-only** traffic simulation and 3D digital twin of the
**Central Silk Board Junction** area in Bengaluru, built to answer one question
rigorously:

> When an ambulance is delayed in traffic, **where** was the time lost, and **how
> much of it** would a different traffic-signal policy have recovered?

The method is **counterfactual replay**: take one fixed scenario — the same road
network, the same background traffic, the same random seed, the same ambulance
origin/destination and dispatch time — and replay it under different signal
policies. Because everything except the policy is held constant, the difference
in travel time is attributable to the policy rather than to noise.

---

## Status

**Phase 2: SUMO road network built and validated.** The OpenStreetMap extract
for Central Silk Board ([`docs/STUDY_AREA.md`](docs/STUDY_AREA.md)) has been
converted with `netconvert` into a SUMO network — 1,515 edges, 664 junctions,
12 traffic lights, one connected component — and structurally validated, with
the flyover verified grade-separated from the ground network at all 8 of its
plan-view crossings. See [`docs/SUMO_CONVERSION.md`](docs/SUMO_CONVERSION.md).

**Phase 2.5 review complete.** Six open items investigated, one conversion
defect corrected, and the remaining uncertainties recorded rather than resolved
by guesswork — see [`docs/SUMO_NETWORK_REVIEW.md`](docs/SUMO_NETWORK_REVIEW.md).
The network is structurally sound; three stated conditions apply to any number
it produces.

**Phase 3: baseline simulation running.** Seven vehicle types, a seeded
boundary-to-boundary demand model and a TraCI baseline run — 3,226 vehicles,
0 teleports, and an ambulance that completes its 29-edge route through the
interchange in 148.5 s with no priority of any kind. See
[`docs/TRAFFIC_DEMAND.md`](docs/TRAFFIC_DEMAND.md) and
[`docs/BASELINE_SIMULATION.md`](docs/BASELINE_SIMULATION.md).

**Phase 4: uncertainty quantified.** Five seeds put the baseline ambulance travel
time at 145.90 s with a standard deviation of **1.14 s** on an identical route —
the noise floor any Phase 5 counterfactual must clear. Sensitivity experiments
rank the estimated assumptions by how much they move the result. See
[`docs/BASELINE_CALIBRATION.md`](docs/BASELINE_CALIBRATION.md) and
[`docs/BASELINE_UNCERTAINTY.md`](docs/BASELINE_UNCERTAINTY.md).

**Phase 5: counterfactual replay, seed 42.** Four signal policies over one
identical scenario. All four gave the same simulated ambulance travel time
(205.5 s, 0.00 s saved) at a traffic cost of 17,270-33,575 vehicle-seconds,
because the ambulance is never stopped by a signal on its route. See
[`docs/COUNTERFACTUAL_REPLAY.md`](docs/COUNTERFACTUAL_REPLAY.md). Seeds 43-46 are
not yet run.

**The demand is assumed, not measured.** No traffic count for Silk Board was
available, and nothing in this project has been validated against observed
Bengaluru traffic. Absolute travel times describe this modelled traffic and exist
to be subtracted from a counterfactual — which is Phase 5, and does not exist
yet. Every module that
would produce a travel time or a delay is still an explicit stub that raises
`NotImplementedError` rather than returning a placeholder — see
[Research integrity](#research-integrity) for why. Both phases also have human
inspection outstanding, listed at the end of each document.

---

## The pipeline

```
  OpenStreetMap extract          documented download, provenance recorded
            │
            ▼
  netconvert / OSMnx             → SUMO road network (.net.xml)
            │
            ▼
  SUMO microsimulation           background traffic, signals, queues
            │
            ▼
  TraCI control loop             per-step state + policy actuation
            │
            ▼
  FastAPI + WebSocket            streams simulation state to clients
            │
            ▼
  React / Three.js / R3F         renders the state SUMO produced
```

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Architecture rule: SUMO is the source of truth

Vehicle positions, speeds, queues, signal phases and travel times originate in
SUMO and nowhere else. The frontend is a **renderer**, not a simulator.

This is not a style preference. If the browser interpolates a vehicle along a
road because it looks smoother, the picture stops being evidence — you can no
longer point at the screen and say "that is what the model produced". Concretely:

- The frontend never invents, extrapolates or animates vehicle motion that SUMO
  did not produce. Visual smoothing between received frames is permitted **only**
  as interpolation strictly between two real simulation states, and it must never
  feed back into any reported measurement.
- The ambulance follows the simulated road network as an actual SUMO vehicle
  subject to traffic, queues and signals. It is not a curve drawn over a map.
- Every reported travel time, delay and "recovered time" is computed from SUMO
  output (`tripinfo`, `fcd`, edge/lane data, TraCI readings) — never estimated,
  never hand-tuned, never hard-coded.

---

## Research integrity

This project is intended to support claims about a real intersection in a real
city. That only works if the difference between *measured*, *sourced* and
*assumed* is visible at all times.

**Every dataset, parameter and reported figure carries one of four labels:**

| Label | Meaning |
|---|---|
| `VERIFIED REAL DATA` | Obtained from a primary source **and** independently checked against that source. Provenance record required. *Nothing currently qualifies.* |
| `PUBLICLY SOURCED DATA` | Taken from a public, citable source (e.g. OpenStreetMap, a published report) but not independently verified. Source URL, access date and licence required. *The Silk Board road network is here.* |
| `ESTIMATED DATA` | A reasoned assumption made by this project because real data was unavailable. The reasoning and its basis must be recorded. **Never presented as real.** |
| `SIMULATED DATA` | Produced by SUMO or downstream analysis in this repository. Reproducible from a saved config + seed. |

**Standing rules:**

1. Never fabricate real-world traffic data.
2. Never present estimated signal timings as observed signal timings. Bengaluru
   signal plans are not published openly; anything we use is `ESTIMATED DATA`
   until a documented source says otherwise.
3. Preserve provenance for everything in `data/` — see
   [`docs/DATA_INTEGRITY.md`](docs/DATA_INTEGRITY.md).
4. Save the simulation configuration **and the random seed** for every run. A run
   whose seed was not recorded is not a result.
5. All reported travel times and delay values come from actual simulation output.
6. Keep measured results and assumptions visually distinct in every output —
   docs, API responses and UI alike.
7. Keep the study area small enough to run reliably: Central Silk Board Junction
   plus roughly 5–10 surrounding intersections.

---

## Repository layout

```
ems-black-box/
├── frontend/     React + TypeScript + Vite + Three.js + R3F + Drei + Tailwind
├── backend/      FastAPI + Pydantic; REST + WebSocket state streaming
├── simulation/   SUMO scenario building, TraCI control loop, signal policies
├── analysis/     NumPy/Pandas delay attribution and intersection ranking
├── data/         raw/ (as downloaded), processed/ (derived), provenance/ (audit)
├── database/     PostgreSQL + PostGIS init scripts and schema
├── docs/         Architecture, data integrity, environment, roadmap
├── tests/        Backend, simulation and analysis tests
├── scripts/      Environment checks and dev entry points
└── docker/       Container assets (PostGIS today; services later)
```

---

## Getting started

### 0. Check your environment

```bash
./scripts/check_env.sh
```

This reports Node, Python, SUMO and Docker status and, importantly, works out
where `SUMO_HOME` should point on this machine. It changes nothing.

### 1. Backend

Uses Python **3.11** (the geospatial stack does not yet have reliable wheels for
3.14, which is the default `python3` on this machine).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --reload --app-dir backend --port 8000
```

Then: <http://localhost:8000/api/health> and <http://localhost:8000/docs>

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Then: <http://localhost:5173>. The page reports whether the backend is
reachable; it does not display simulation data, because there is none yet.

### 3. Ingest the study area

```bash
source .venv/bin/activate
pip install -r simulation/requirements.txt      # osmnx, geopandas, shapely, pyproj
python scripts/ingest_study_area.py --dry-run   # show the plan, fetch nothing
python scripts/ingest_study_area.py             # download, validate, write
python scripts/plot_study_area.py               # render the network for review
```

Needs outbound access to an Overpass instance or the OSM API. If none is
reachable the pipeline fails loudly and writes nothing — there is no synthesised
fallback. Supply an extract yourself with `--from-file` in that case.

### 4. Build the SUMO network

```bash
python scripts/build_sumo_network.py             # convert + validate + provenance
python scripts/build_sumo_network.py --dry-run   # print the netconvert options
python scripts/plot_sumo_network.py              # render the network for review
```

Needs SUMO installed. The pipeline resolves the headless binaries by absolute
path — on macOS the `sumo` on `PATH` is the GUI launcher and would silently
simulate nothing.

### 5. Run the baseline simulation

```bash
python scripts/run_baseline.py --dry-run     # show the plan
python scripts/run_baseline.py               # generate demand, route, simulate
python scripts/calibrate_demand.py           # sweep demand scales
```

### 6. Calibration and uncertainty

```bash
python scripts/run_multi_seed_baseline.py    # 5 seeds, aggregate statistics
python scripts/verify_determinism.py         # same seed twice
python scripts/run_sensitivity.py            # vehicle mix + behaviour variants
python scripts/audit_baseline.py             # signal programs + speed provenance
```

### 7. Tests

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

### 8. Database (optional, not required yet)

Nothing depends on Postgres yet. When you want it:

```bash
docker compose --profile database up -d
```

---

## Where to read next

- [`docs/STUDY_AREA.md`](docs/STUDY_AREA.md) — the Phase 1 result: the bounding
  box and how it was derived, what the extract contains, and the eight
  limitations that must travel with any result built on it.
- [`docs/SUMO_CONVERSION.md`](docs/SUMO_CONVERSION.md) — the Phase 2 result: the
  netconvert configuration, why grade separation is topological rather than
  vertical, every warning investigated, and what still needs a human in netedit.
- [`docs/EMS_SIGNAL_POLICIES.md`](docs/EMS_SIGNAL_POLICIES.md) — the four
  policies, how priority is granted without ever creating a conflicting green,
  and which traffic lights are actionable.
- [`docs/COUNTERFACTUAL_REPLAY.md`](docs/COUNTERFACTUAL_REPLAY.md) — the seed-42
  paired results and what they do and do not show.
- [`docs/EMS_DELAY_ATTRIBUTION.md`](docs/EMS_DELAY_ATTRIBUTION.md) — where the
  ambulance's time actually goes.
- [`docs/BASELINE_CALIBRATION.md`](docs/BASELINE_CALIBRATION.md) — what the
  baseline rests on, how sensitive it is to each assumption, and why nothing was
  tuned.
- [`docs/BASELINE_UNCERTAINTY.md`](docs/BASELINE_UNCERTAINTY.md) — the multi-seed
  spread, and what it does and does not cover.
- [`docs/TRAFFIC_DEMAND.md`](docs/TRAFFIC_DEMAND.md) — the Phase 3 demand model:
  vehicle types, flow rates, and how the demand scale was calibrated after the
  first run gridlocked.
- [`docs/BASELINE_SIMULATION.md`](docs/BASELINE_SIMULATION.md) — the baseline
  run: what it measures, what it refuses to measure, and what its numbers are not.
- [`docs/SUMO_NETWORK_REVIEW.md`](docs/SUMO_NETWORK_REVIEW.md) — the Phase 2.5
  review: operational status, traffic-light mapping, ramp topology, junction
  merges, and the full inventory of netconvert-supplied lane counts and speeds.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how OSM → SUMO → TraCI →
  FastAPI/WebSocket → React/Three.js fits together, including coordinate frames.
- [`docs/DATA_INTEGRITY.md`](docs/DATA_INTEGRITY.md) — the four data labels and
  the provenance record format.
- [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) — this machine's toolchain, and
  the SUMO framework-install quirk that breaks headless runs if ignored.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — the incremental build order.
