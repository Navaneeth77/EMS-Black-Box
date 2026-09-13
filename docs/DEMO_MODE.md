# Demo mode

A presentation scenario for the 3D digital twin, kept strictly separate from the
frozen research experiment.

> **Demo numbers are not research results.** The research result is the frozen
> 5-seed × 4-policy experiment in [`FINAL_RESULTS.md`](FINAL_RESULTS.md). Demo
> mode runs a *different* scenario — denser demand and four simultaneous
> obstructions — so its travel times and savings are larger and must never be
> quoted in place of the research figures.

> **HISTORICAL_DEMO is now the default presentation scene.** Its demand is
> anchored to published Silk Board counts, and it runs on a four-way controller
> network variant. See [`HISTORICAL_DEMO.md`](HISTORICAL_DEMO.md). The DEMO scene
> described here is unchanged and still loads at `?scene=demo`.

---

## 1. Research mode vs demo mode

| | RESEARCH | DEMO |
|---|---|---|
| Purpose | the paper's numbers | showing the mechanism |
| Demand scale | 0.5 (Phase 4 calibration) | **0.8** |
| Disturbance | one obstruction, hash `3c43dded423b95d7` | four obstructions, set hash `0ccc23a8077ee1fe` |
| Seeds × policies | 5 × 4, frozen | seed 42; NORMAL and EMS_ROLLING |
| Run length | full 3,900 s window | **stops at the ambulance's SUMO arrival** |
| Outputs | `data/processed/silk_board_v1/counterfactual/inc_*` | `simulation/results/demo_*`, `frontend/public/scene/` |
| Demand id | `cf_silk_board_v1_two_signal_seed{n}` | `demo_silk_board_v1_two_signal_seed42_s0.8` |
| Manifest `mode` | `RESEARCH` | `DEMO` |

Everything that makes demo mode different is opt-in and off by default in the
shared code: `stop_on_ambulance_arrival=False`, `record_signal_timeline=False`,
and a single `IncidentConfig` still routes through the original controller. A
research run cannot enter a demo path by accident. Both modes use the same SUMO
network, the same signal programs, the same vehicle types, the same ambulance
trip and the same EMS state machine.

## 2. Real traffic data — none used in DEMO mode, and why

*(HISTORICAL_DEMO does use published historical Silk Board counts. The live and
time-series feeds below remain unusable. See [`HISTORICAL_DEMO.md`](HISTORICAL_DEMO.md).)*

External traffic data for Central Silk Board was investigated before any demand
was changed. Full record: [`data/traffic/provenance/traffic_data_investigation.json`](../data/traffic/provenance/traffic_data_investigation.json).

| Source | Result |
|---|---|
| TomTom Traffic Flow API | HTTP 401 — licensed key required, with redistribution conditions this project cannot meet |
| data.gov.in | no reachable machine-readable traffic resource |
| Bengaluru Traffic Police / B-SMILE | project PDFs, not time series |
| GitHub "Bengaluru traffic" CSV (275 Silk Board rows) | **fetched, tested, rejected as synthetic** |

The CSV was the only candidate with Silk Board coverage, and it fails a basic
realism test: its **weekday/weekend volume ratio is 0.984** — no weekly structure
at all, where real arterial traffic drops substantially at weekends. It also
reports 81.9 km/h average speed at India's most congested junction, has no
licence and no stated source. Presenting it as "historical traffic data" would
have been a fabrication.

**So no REAL-TIME or HISTORICAL data is used anywhere.** Demand is this project's
own model, `ESTIMATED_DATA`, and every vehicle state is `SIMULATED_DATA`. The UI
says so.

## 3. How the congestion is produced

No vehicle is placed, moved, stopped or rerouted by demo code. Congestion comes
from two changes to the *inputs*, and SUMO produces everything that follows.

**Denser demand.** Scale 0.8 instead of 0.5. Phase 4 found scale 1.0 gridlocks
this network (148 teleports, 955 vehicles never entering), so the demo stays
below it. Result: **425 vehicles in the network on average, 499 at peak, 1,071
distinct** over the demo window — against 184 / 198 / 468 in the research scene.

**Four obstructions**, each a partial lane obstruction (lane stays open, speed
drops to a crawl), each on a lane **measured** to carry traffic — the research
phase learned the hard way that an obstruction on an empty lane does nothing:

| Incident | Lane | Vehicles observed | Window | Role in the story |
|---|---|---|---|---|
| `demo_gs_approach` | `1393474724#1_1` | 28 | 560–980 s | the signal approach the research attributes delay to |
| `demo_gs_conflicting` | `464465165#3_1` | 18 | 520–1,020 s | a conflicting approach — the traffic the green corridor holds back |
| `demo_downstream` | `1148717038#0_1` | 39 | 540–1,000 s | congestion after the junction, so priority is visibly not the whole answer |
| `demo_route_tail` | `1416769967_0` | 61 | 560–980 s | a second visible queue away from the main junction |

## 4. What the demo shows (seed 42)

**OBSERVED in the demo scenario:**

| | Baseline (NORMAL) | EMS_ROLLING |
|---|---|---|
| Ambulance travel | **493.0 s** | **360.0 s** |
| Waiting | 125.0 s | 0.0 s |
| Stops | 4 | 0 |
| SUMO arrival | t = 1,093.5 s | t = 960.5 s |
| Time saved | — | **133.0 s (27.0%)** |

The saving shown in the HUD is computed from the demo's own paired NORMAL run and
is labelled *"demo paired run (NOT the research experiment)"*. An earlier export
displayed the *research* seed-42 saving (78.5 s) against demo traffic; that
contamination was caught and removed (see `COUNCIL_LOG.md`).

## 5. How EMS priority is made visible — without faking it

Three layers, all driven by what the run recorded:

1. **Signal lamps show the applied SUMO state.** The run records every traffic
   light's state from TraCI on change, before the policy acts each step, and the
   renderer replays it. Earlier the renderer rebuilt lamp colours from the static
   program, which is correct for a NORMAL run but **wrong for an EMS run**, where
   the policy holds and truncates phases — the green corridor would never have
   appeared on the lamps. Reconstruction from the program is now only a fallback
   for scenes exported without a recording.
2. **Priority state comes from the recorded transitions.** A signal is marked
   *priority requested* or *priority active* only if the policy actually entered
   that state at that time. A small "EMS PRIORITY" label appears above a held
   signal; a thin corridor and a pulse connect the ambulance to the signal it is
   being served by. Nothing anticipates a state.
3. **Safety is inherited, not re-implemented.** The policy only selects among the
   program's own phases, never truncates a yellow or all-red, and every signal
   state is validated against its program's phase set on every step. Conflicting
   approaches go red because the phase serving the ambulance is a phase in which
   they are red — the controller's own design, not a frontend override.

### Verified in the recorded applied states

Checked against what TraCI reported, not against the program:

| Check | Result |
|---|---|
| Traffic lights with a recorded applied state | **8 / 8** (288 state changes) |
| Ambulance's own movement green during PRIORITY_ACTIVE | **12 / 12** probes |
| Other movements held red at the same instants | **12 / 12** probes |
| Signal states outside a program's phase set | **0** (runner validates every step) |
| Demo re-simulation reproduces the earlier export | **exact** — 360.0 / 0.0 / 0 and 493.0 / 125.0 / 4 |

## 6. Arrival stops the simulation

Two places, both keyed to SUMO's own arrival event:

* **Simulation.** In demo mode the TraCI loop breaks on the first step after the
  ambulance appears in `getArrivedIDList()`. It does not keep simulating hundreds
  of seconds of traffic nobody is watching.
* **Playback.** The exported trajectory window ends 12 s after arrival, and the
  shared clock clamps to the recorded arrival time and pauses — it does not wrap.
  The scene freezes and an **EMS response complete** card shows travel time,
  waiting, stops, time saved and policy, all read from the run.

Research runs never stop early: their traffic metrics are measured over the whole
window, and truncating it would change every traffic figure's denominator.

## 7. Interface

The default view is a compact **EMS Response** card — status, elapsed, speed,
waiting, stops, policy, time saved — and a single control bar with play, replay,
timeline and three cameras. **Nothing was deleted.** Provenance, scenario
metadata, the disturbance record, per-signal attribution and the paired
comparison moved into a **Research details** drawer, and the full engineering HUD
is one click away under **Expert view**.

Cameras: **Overview**, **Chase** (behind the ambulance along its own heading) and
**Priority** (jumps to the first recorded priority grant and frames that signal).
In demo mode an auto-director opens on the busy network and cuts once to the
chase shot when SUMO has the ambulance in the network; choosing a camera by hand
turns it off.

## 8. Vehicles and flyover

**Ambulance** rebuilt as a box-body ambulance: stepped patient compartment,
lower cab with raked windscreen, livery band, roof light bar with red and blue
lenses, headlights, tail lights, wheel arches and hubbed wheels. Length and width
remain the SUMO vType's. It is authored geometry, recorded as such in
[`data/provenance/3d_assets.json`](../data/provenance/3d_assets.json), and is
**not** a downloaded GLB.

**Flyover** rebuilt from the network's own elevated lanes: deck underside with
depth, parapet walls on the outer edges of each deck (not between its own lanes),
and piers with caps along the deck centreline. Plan position and height ordering
come from the network and OSM layer tags; pier spacing and parapet height are
rendering choices.

A real grade-separation defect was found while validating the denser demo scene:
junction connector lanes were lifted to deck height at any junction that also
touched an elevated road, so vehicles turning at street level popped 6 m into the
air and dropped back. Connector elevation now comes from the roads a connector
actually joins.

## 9. Limitations

* **No real traffic data.** Demand is estimated; the one Silk Board dataset found
  was synthetic.
* **One seed, two policies.** The demo shows the mechanism; the research
  experiment establishes the effect.
* **Denser demand and four obstructions are presentation choices.** They make the
  mechanism visible; they are not claims about Silk Board traffic.
* **Signal programs remain netconvert-generated**, so the timing on screen is an
  assumption, not Bengaluru's.
* **Vehicle models are authored.** No suitable licensed GLB set was obtainable.
* **The priority corridor and pulse are wall-clock animations** shown only while
  a recorded priority state holds. They have no counterpart inside SUMO.
