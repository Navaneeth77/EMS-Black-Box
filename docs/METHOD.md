# Method

How the question is asked, what is held fixed, and what each number is allowed to
mean. The results themselves are in [`RESULTS.md`](RESULTS.md); what they cannot
support is in [`LIMITATIONS.md`](LIMITATIONS.md).

---

## 1. The design: one scenario, one variable

> When an ambulance is delayed in traffic, how much of that delay would a
> different signal policy have recovered?

The design is a **paired counterfactual**. One scenario is built — a road
network, a demand, a seed, an ambulance trip, and optionally a disturbance — and
replayed under several signal policies. Everything except the policy is byte-for-
byte the same, so the difference between two runs is the policy or it is nothing.

That is enforced rather than trusted. `ems_sim/counterfactual/pairing.py` hashes
the scenario — network, demand config, demand id, seed, window, vehicle mix,
ambulance origin/destination/departure, route, variant and **disturbance** — and
refuses to pair two runs whose hashes differ:

```
scenario_hash   identical   in both arms   ← proves nothing else changed
policy_hash     different   between arms   ← proves something did
```

A pair that matches on both is two copies of one run; a pair that differs on the
first is two different scenarios. Both are refused.

## 2. SUMO is the only source of truth

The simulator decides what happened. Nothing downstream may invent, smooth or
correct it.

- The ambulance is an **ordinary SUMO vehicle**. It is never moved with
  `moveToXY`, never teleported by this project's code, never given a speed
  advantage. A policy may change a traffic light and nothing else.
- Every measurement is read from SUMO: arrivals from its arrival events, waiting
  and time loss from `getAccumulatedWaitingTime` / `getTimeLoss`, queues from
  `getLastStepHaltingNumber`, signal states from `getRedYellowGreenState`.
- The 3D replay is a **renderer of a recording**. Vehicle positions come from
  SUMO's FCD output; signal lamps replay the states SUMO applied, not a
  reconstruction from the static program. `scripts/validate_scene_coords.py`
  checks the scene against the FCD — round trip, georeference, scale, heading,
  continuity, elevation — and checks **every frame** of the ambulance against
  SUMO's own record of it, so the vehicle the demo is about cannot be a
  different trajectory.

## 3. What is observed, what is derived, what is simulated

Every value in this project carries one of four labels, and they are never mixed:

| Label | Meaning | Example |
|---|---|---|
| `OBSERVED` | Measured by someone else and cited | 22,634 vehicles in the Silk Board peak hour (CMP 2019, Table 2-13) |
| `PUBLICLY_SOURCED` | Taken from a public source, not independently verified | the OpenStreetMap road network |
| `DERIVED` | Computed from an observed value by a stated rule | SUMO input flow = observed peak hour × k |
| `ESTIMATED` | Chosen by this project because no source exists | signal phase splits, vehicle mix, demand scale |
| `SIMULATED` | Produced by SUMO | every travel time, queue and trajectory in this repository |

**No ambulance GPS trace was available**, and none is simulated as if it were.
The ambulance's origin, destination and departure are configuration choices,
labelled as such wherever they appear.

## 4. The measurement rules

### The ambulance

One vehicle, so the rule is simple and strict. A run is **invalid for headline
use** if the ambulance failed to arrive, was teleported, produced a signal state
the program does not define, or produced an unsafe signal state or change. Time
saved is `NORMAL travel time − policy travel time` for the same seed.

### The traffic

Thousands of vehicles, and the rule that matters is *which* vehicles.

Summing a delay metric over "the vehicles that arrived" gives each arm a
different denominator. A policy that holds cross traffic longer leaves more of it
unfinished at the horizon — and those are the vehicles it delayed most, so they
drop out of its own total and it can look cheaper for being more expensive. The
audit measured this: it changes the **sign** of the result in 4 of 30 pairs and
the magnitude by up to a factor of three.

So every generated vehicle is classified in both arms before any metric is
computed (`ems_sim/counterfactual/cohort.py`):

```
generated ─┬─ completed
           ├─ completed_after_teleport   (counted, excluded from travel-time metrics)
           ├─ unfinished                 (still running at the horizon; delay censored)
           ├─ never_departed             (insertion backlog)
           └─ missing                    (must be zero)
```

and four views are reported side by side:

1. **all completed** — each arm's own survivors. Survivorship-biased; kept only
   so the size of the bias is visible.
2. **completed, not teleported** — a teleported vehicle did not drive the
   distance it is credited with.
3. **paired common cohort** — the vehicles that completed without a teleport in
   *both* arms. **This is the only view a causal claim may rest on.**
4. **censored** — what (3) had to leave out, per arm and per reason. A policy
   that finishes fewer vehicles has not made the network faster.

### Attribution

Per-edge traversal times are compared between the paired runs. Delay on a
signal's approach edge is that signal's. Delay on an edge *behind* a signal is
attributed to it only when a queue was recorded standing at that signal while the
ambulance was on that edge; otherwise it stays in an explicit `unattributed`
bucket. Attribution refuses to run at all if the two arms drove different routes.

## 5. The policies

All of them work the same way: they **select among the phases netconvert already
generated**, never writing a signal state string. To reach a priority phase a
policy shortens the current green — never below its minimum, never at all during
a yellow or all-red — and lets the program advance through its own interphases.
Releasing is the reverse: the policy stops extending and the program resumes.

| Policy | Activation | min green | hold extension | max priority |
|---|---|---|---|---|
| `NORMAL` | never (control) | — | — | — |
| `EMS_NEXT` | next actionable signal within 250 m | 5 s | 5 s | 60 s |
| `EMS_ROLLING` | every actionable signal within 700 m | 5 s | 5 s | 60 s |
| `EMS_FULL_SCOPE` | every actionable signal, no distance test | 5 s | 5 s | 60 s |
| `EMS_FULL_PREEMPTION` | every actionable signal, no distance test | 3 s | 3 s | 600 s |

`EMS_FULL_SCOPE` exists because `EMS_FULL_PREEMPTION` differs from `EMS_ROLLING`
in three parameters at once, so their difference cannot be read as the value of
activation scope. `ROLLING → FULL_SCOPE` changes scope alone.

Their activation rule is a **fixed distance**, and is labelled
`DEMO_FIXED_ACTIVATION_DISTANCE` in every report. A distance is not a lead time:
700 m is 36 s away at 70 km/h and 175 s at 14 km/h. A genuinely predictive rule —
one that estimates the signal's transition time, the queue's clearance time and a
margin, and asks when the ambulance's ETA no longer leaves room for them — exists
separately as `EMS_PREDICTIVE` in the HISTORICAL_DEMO pipeline, and labels itself
`PREDICTIVE_ETA_ACTIVATION`.

## 6. The state machine, and what it records

```
NORMAL ─► DETECTED ─► REQUESTED ─► TRANSITIONING ─► PRIORITY_ACTIVE
                                                          │
          NORMAL ◄─ RESTORING ◄─────── CLEARING ◄─────────┘
```

The separations are the point: *seen* is not *asked for*, *asked for* is not *the
signal moved*, and *the ambulance passed* is not *normal service restored*. Each
encounter between the route and a signal carries the time of every stage, the
distance and speed at the request, the signal's response time, and how long
priority was held.

Two independent checks run on every signal, every step:

- the applied state must be one the program defines (a state that is not means
  something wrote a state string directly);
- the applied state and every change between states must be safe against the
  junction's own foe matrix, read from the network through sumolib. Two protected
  greens on conflicting links, or a protected green taken while a conflicting one
  drops to red with no yellow, fails the run. A permissive green (`g`) beside a
  conflicting protected green is give-way, not a conflict.

## 7. Queues are measured, not inferred

"Priority was granted and the queue discharged" is a claim about the queue. It is
measured from SUMO's halting count on each signal's approach lanes, sampled
through the run and read at the moments the policy's own timeline names: at the
request, when the signal answered, when the ambulance passed, and after it had
gone. The ambulance moving is not evidence that a queue cleared.

## 8. Reproducing it

```bash
pip install -e ".[backend,dev]"          # SUMO itself: see ENVIRONMENT.md
python scripts/ingest_study_area.py      # OSM extract (not committed; ODbL)
python scripts/build_sumo_network.py     # netconvert -> the research network
python scripts/run_counterfactual.py --seed 42 --incident
python scripts/analyse_paired_cohort.py  # the cohort re-analysis
```

Each script writes a provenance record next to its output: the SUMO version, the
network hash, the scenario hash, the seed and the input files. `data/processed/`
holds the committed results; `simulation/results/` holds the raw SUMO output and
is not committed (3.3 GB), but every figure quoted in `RESULTS.md` comes from a
file that is.
