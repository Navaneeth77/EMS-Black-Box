# EMS Black Box

A software-only traffic simulation and 3D replay of **Central Silk Board
Junction**, Bengaluru, built to answer one question as carefully as a simulation
can:

> When an ambulance is delayed in traffic, **where** was the time lost, and **how
> much of it** would a different traffic-signal policy have recovered?

Everything below is about a **model**. No real ambulance, real dispatch record or
real GPS trace is involved — see [§6](#6-the-limitation-that-matters-most).

---

## 1. What this is

A SUMO microsimulation of the junction and its approaches, driven by demand
derived from a published traffic count, with an ambulance routed through it under
different traffic-signal policies. Around it:

- a **counterfactual harness** that replays one scenario under several policies
  and refuses to compare two runs that are not the same scenario;
- a **measurement layer** that reports what happened to every vehicle, not only
  the ones that finished;
- a **3D replay** (React + Three.js) that renders SUMO's recorded output — it
  decides nothing and invents nothing.

## 2. What is observed

Three things, each cited, none of them a trajectory:

| Observed | Value | Source |
|---|---|---|
| Peak-hour junction volume | 22,634 vehicles / 18,180 PCU | Comprehensive Mobility Plan for Bengaluru (Draft, Oct 2019), Table 2-13, citing a Dec 2014 – Apr 2015 survey |
| Car share of vehicles | 53% | IJIRSET, June 2017 |
| Signal cycle length | 450 s | IJIRSET, June 2017 |

The road network is **publicly sourced**: an OpenStreetMap extract (© OpenStreetMap
contributors, ODbL 1.0), converted with `netconvert`.

Everything else that a traffic study would measure — turning movements, phase
splits, the non-car vehicle mix, time-of-day profiles — **was not available and
is not invented**. It is labelled `ESTIMATED` wherever it appears.

## 3. What is simulated

Every travel time, queue, trajectory and signal state in this repository. SUMO
produces them; nothing downstream edits them. The 3D scene is a renderer of
SUMO's own FCD recording, and a validator checks the scene against that recording
frame by frame — including every frame of the ambulance, so the vehicle the demo
is about cannot quietly become a different trajectory.

## 4. What the counterfactual is

One scenario — one network, one demand, one seed, one ambulance trip, one
disturbance — replayed under several signal policies, with **only the policy
different**. That is enforced by hashing: paired runs must share a
`scenario_hash` (network, demand, seed, window, vehicle mix, ambulance trip,
route, disturbance) and must differ in `policy_hash`. A pair that fails either
test is refused rather than reported with a caveat.

Policies range from a conventional single-junction preemption (`EMS_NEXT`) to an
unrealistic upper bound that holds every signal for the whole trip
(`EMS_FULL_PREEMPTION`). Method: [`docs/METHOD.md`](docs/METHOD.md).

## 5. Current result

**Signal priority is worth little when the ambulance is already moving, and
substantially more when congestion has put a queue in front of it.** In the
free-flowing scenario it recovers about 12 s of a ~185 s trip; with the modelled
queue-producing disturbance it recovers tens of seconds, because there is a queue
for it to discharge.

The network-wide delay figures are **not** a cost of priority, and are labelled
`DIAGNOSTIC_ONLY`: two ambulance-free controls and a corrected paired-cohort
analysis all show effects of the same size arising with no ambulance present.

Numbers, with what changed in the 2026-09-14 audit and why:
[`docs/RESULTS.md`](docs/RESULTS.md).

## 6. The limitation that matters most

**No real ambulance GPS trace, dispatch log or response-time record was available
for Bengaluru, and none is simulated as if it were.** The ambulance's origin,
destination and departure time are choices made by this project so that it passes
through the junction under study.

Nothing here establishes what signal priority would do for a real ambulance at
Central Silk Board. The full list — one route, one junction pair, five seeds,
netconvert-generated signal plans, censored vehicles, and a traffic-side metric
that measures the model's sensitivity rather than the policy — is in
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## 7. Reproducing it

Python **3.11** (the geospatial stack has no reliable 3.14 wheels) and a working
SUMO installation — [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md), and
`./scripts/check_env.sh` reports what is missing without changing anything.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,dev]"

python scripts/ingest_study_area.py       # OSM extract (downloaded, not committed)
python scripts/build_sumo_network.py      # netconvert -> the research network
python scripts/run_counterfactual.py --seed 42 --incident
python scripts/analyse_paired_cohort.py   # cohort analysis of the frozen matrix

pytest -m "not sumo"                      # what a machine without SUMO can check
cd frontend && npm ci && npm run typecheck && npm run test && npm run dev
```

Networks (`*.net.xml`), routes, raw SUMO output and the exported 3D scene are
**generated, not committed** — they are large and reproducible. Every aggregate
result and validation report quoted anywhere in these docs **is** committed under
`data/processed/`.

## Repository layout

```
ems-black-box/
├── simulation/   SUMO scenario building, the TraCI control loop, signal policies,
│                 the counterfactual harness and its measurement layer
├── frontend/     React + TypeScript + Vite + Three.js — replay of a recording
├── backend/      FastAPI service layer
├── scripts/      Every pipeline entry point, one concern each
├── data/         raw/ (downloaded), processed/ (results + validation),
│                 traffic/ (observed counts with provenance), provenance/ (audit)
├── docs/         METHOD, RESULTS, LIMITATIONS, and the topic references;
│                 archive/ holds the phase logs and earlier reports
└── tests/        Python test suite
```

## Where to read next

| If you want | Read |
|---|---|
| How the question is asked and what each number may mean | [`docs/METHOD.md`](docs/METHOD.md) |
| The numbers, and what the audit changed | [`docs/RESULTS.md`](docs/RESULTS.md) |
| What this cannot support | [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) |
| The 3D replay of observed-demand traffic | [`docs/HISTORICAL_DEMO.md`](docs/HISTORICAL_DEMO.md) |
| Why there is no LICENSE yet | [`docs/LICENSING.md`](docs/LICENSING.md) |
| Every review cycle and defect found | [`docs/archive/COUNCIL_LOG.md`](docs/archive/COUNCIL_LOG.md) |

---

*Simulated results only. Not a measurement of real ambulance performance in
Bengaluru.*
