# How to read this project's numbers

Three kinds of statement appear in the EMS Black Box results, and they carry
very different weight. Collapsing them is how a simulation becomes a claim about
a city.

| | What it is | How far it travels |
|---|---|---|
| **OBSERVED SIMULATION RESULT** | A number SUMO produced under a declared configuration and seed. Reproducible from the committed config. | Only to "under this scenario, the simulation did X." |
| **MODELLING ASSUMPTION** | An input this project chose because no source was available. Labelled `ESTIMATED_DATA` or `SIMULATED_SCENARIO`. | Nowhere on its own. It conditions every result downstream of it. |
| **RESEARCH INTERPRETATION** | What the observed results, read against the assumptions, appear to mean. | As far as the assumptions allow, and no further. |

---

## What is OBSERVED

These are measurements of the simulation, not of Bengaluru:

* Ambulance travel time, waiting time, time loss, stops, per-edge traversal.
* Signal state at any simulation time, and every policy state transition.
* Halting vehicle counts and SUMO's own `queueing_length`.
* Vehicles departed, arrived, still running, teleported.
* Whether the ambulance was held at a red on **its own** controlled movement.

Each is reproducible from `(network hash, demand config hash, seed, policy,
incident hash)`, all of which are recorded with every run.

## What is ASSUMED

Every one of these is a choice this project made because no source existed. They
are not neutral: each conditions everything computed on top of it.

| Assumption | Class | Consequence if wrong |
|---|---|---|
| Signal programs (netconvert-generated, uniform 90 s cycle) | `ESTIMATED_DATA` | The single most load-bearing input. Sets the red the ambulance waits at, and therefore the delay a policy can recover. |
| Demand scale 0.5, calibrated to the model's own capacity | `ESTIMATED_DATA` | Sets how much traffic the ambulance meets at all. |
| Vehicle mix (50% motorcycle etc.) | `ESTIMATED_DATA` | Drives saturation flow, so it propagates into every queue. |
| 1,375 of 1,515 edge speeds are SUMO defaults; 166 at ≥ 80 km/h | `ESTIMATED_DATA` | Time loss measured against free-flow is over-estimated on those edges. |
| Ambulance origin, destination and departure time | `ESTIMATED_DATA` | A configuration choice, not a dispatch record. |
| The incident: location, lane, timing, severity | `SIMULATED_SCENARIO` | A hypothetical, not a record of any real incident. |
| Road elevation from OSM `layer` at 6 m/step | `ESTIMATED_DATA` | Visual ordering only; no height in the 3D scene is a measurement. |
| Building heights (≈99% estimated) | `ESTIMATED_DATA` | Context massing only. |
| Vehicle 3D models (authored, not GLB assets) | authored | Appearance only. Lengths and widths come from the vTypes. |

**Nothing in this project is `VERIFIED_REAL_DATA`.** The HUD shows that class
greyed out precisely so its absence is visible.

## What may be INTERPRETED

Permitted, because it follows from the observations under the stated assumptions:

* "Under this simulation scenario, policy X reduced simulated ambulance travel
  time by Y s relative to its paired NORMAL run."
* "The saving corresponds to an identified event — a halt at a red on the
  ambulance's own movement — which the policy removed."
* "The policies executed without a signal conflict in any run."
* "The network-wide traffic term is not attributable to the ambulance," because
  two controls reproduced it with no ambulance present.

Not permitted, and not claimed anywhere:

* Any statement about real ambulances, real travel times or real delay in
  Bengaluru.
* "Signal priority saves N seconds" as a transferable figure. The magnitude
  depends on arrival phase within the 90 s cycle and on whether the ambulance
  meets a red at all.
* "EMS priority improves general traffic." The controls disprove the
  attribution.
* Any significance claim. Five seeds, reported as descriptive statistics.

## The rule this project keeps

An observed result that is inconvenient is still an observed result. Two are
preserved rather than tuned away:

1. The traffic-side metric has **no stable sign** across seeds, so it is
   `DIAGNOSTIC_ONLY` and is not reported as a cost or benefit of priority.
2. On the undisturbed scenario the policies were **indistinguishable** — all
   three saved ~12.5 s — because the route offered only one recoverable stop.

Neither was removed to make the story cleaner. Where a scenario was changed, the
reason was that the *instrument* was broken — a disturbance that blocked a lane
carrying zero vehicles is not a null result, it is a defect — and the change is
recorded in `archive/COUNCIL_LOG.md` with the measurement that forced it.
