# HISTORICAL_DEMO — a SUMO replay driven by historical observed Silk Board demand

HISTORICAL_DEMO is a presentation mode for the 3D digital twin. Its demand is
anchored to **published** traffic counts at Central Silk Board Junction; the
ambulance is **caught in a queue** at a **real signalised four-way intersection**
in the SUMO network; and EMS priority is requested far enough ahead that the
corridor clears before the ambulance gets there. NORMAL and EMS PRIORITY replay
side by side on one clock.

> **HISTORICAL_DEMO is not the research result.** The frozen research experiment
> (20 runs, seeds 42–46 × 4 policies) and its figures in
> [`archive/FINAL_RESULTS.md`](archive/FINAL_RESULTS.md) were not modified, regenerated or re-run.
> Validation check C22 compares all 178 frozen research files against SHA-256
> hashes taken before this work began: 0 changed, 0 added. HISTORICAL_DEMO has its
> own network variant, policy, trip, demand and output namespace, and its numbers
> must never be quoted as research findings.

> **Vehicles on screen are simulated.** SUMO trajectories are not historical GPS
> traces, and signal states are not observed signal logs. Only the demand level,
> the car share and the cycle length come from historical observations.

---

## 1. Historical data

All files are in [`data/traffic/historical_central_silk_board/`](../data/traffic/historical_central_silk_board/):
`observed_counts.json` / `.csv` (OBSERVED values only), `provenance.json` (every
source investigated, used or not), `source_notes.md`, and
`demand_conversion.json` (the labelled OBSERVED → SUMO chain).

| Role | Source | What it gives | Label |
|---|---|---|---|
| Junction volume | *Comprehensive Mobility Plan for Bengaluru* (Draft, Oct 2019), Table 2-13, printed p. 2-24 | Silk Board Junction, 4-legged: **peak hour 22,634 vehicles / 18,180 PCU; 24 h 323,099 vehicles / 281,521 PCU** | OBSERVED |
| When and how counted | *Revised Master Plan for Bengaluru 2031 (Draft)*, Vol. 3 (BDA), §4.3 | Traffic surveys **Dec 2014 – Apr 2015**; turning volume counts at 14 locations, **24 h for one day** | OBSERVED |
| Composition and signal | Vani A, Madhu Singh, Prem Swaroup Reddy M, *IJIRSET* 6(6), **June 2017**, p. 10540 | **Cars 53%** of vehicle volume (scope caveat below); **existing cycle length 450 s** | OBSERVED |

Investigated and **not used** (`provenance.json` records why): the BBMP tunnel DPR
(Aug 2024; its nearest count is mid-block on NH-44 near Madiwala, not the
junction); the B-SMILE elevated-corridor DPR (Apr 2025, Indiranagar–Domlur road);
CTTP 2011 (no junction count); the Urban Mobility India "Traffic Survey and
Analysis – Central Silk Board Junction" study (not located in three searches); an
IJRESM 2019 paper (HTTP 403, content unverified); Wikipedia (no counts); Deccan
Herald (HTTP 403).

**Not available in any accessible source, and not manufactured:** vehicle classes
other than cars; turning movement counts or proportions; per-arm volumes; the
phase plan or green times; the exact survey day; the peak hour's clock time.

### OBSERVED / DERIVED / ESTIMATED / SIMULATED

| Label | Items |
|---|---|
| **OBSERVED** | The four junction volumes, each in its published unit and period. The survey window and count duration. Junction type. Car share 53%. Cycle length 450 s. |
| **DERIVED** | Ratios within Table 2-13, context only, never used for demand: peak hour / day = 7.005%; PCU per vehicle 0.803 (peak) and 0.871 (day); mean hourly volume 13,462 veh/h. |
| **ESTIMATED** | Demand scale **k = 0.25** and the ambulance's **700 s** departure. OD spread (the research OD weights). Non-car split. Applying the 2017 car share to the 2014–15 volume. The four-way controller and its split, amber, all-red and phase order. Time-to-teleport 600 s. The trip. Ramped structure ends. The predictive policy's margins. |
| **SIMULATED** | Every vehicle position, speed and heading. Every signal state. All queues, waiting and travel times, including NORMAL vs EMS. |

The four volumes are never interchanged: `sources.py` selects each by id **and**
asserts its unit string, and a test proves a PCU figure cannot be read as vehicles.

---

## 2. Pipeline

```
historical data  → composition → turning proportions → demand conversion → routes
(CMP/RMP, IJIRSET) (car 53% OBS)  (none observed:        (22,634 veh/h × k)   (duarouter,
                                    research OD weights)                        seed 42)
      → NORMAL / EMS_PREDICTIVE runs → recorded simulation (FCD + TraCI signal
        timeline + policy transitions) → 3D replay (SINGLE / COMPARE)
```

1. **OBSERVED** — 22,634 vehicles in the peak hour. Vehicles, not PCU; peak hour, not 24 h.
2. **DERIVED** — the context ratios above; none drives the demand.
3. **ESTIMATED — the scale and the departure**, chosen together by the rule in §3.
   At k = 0.25 SUMO is given **5,658.5 veh/h**.
4. **ESTIMATED — the OD spread.** The 14 committed research OD pairs keep their
   relative weights and are multiplied by **1.1548** so they total the step-3 input.
   No Silk Board turning movement count exists to do better.
5. **Composition.** Cars are exactly **53%** of every flow (OBSERVED share,
   preserved). The other 47% follows the research model's ESTIMATED proportions:
   motorcycle 0.3176, auto 0.0889, van 0.0254, bus 0.0191, truck 0.0191.
6. **SIMULATED.** duarouter routes **7,398** vehicles (3,905 cars, 2,344
   motorcycles, 661 autos, 193 vans, 147 buses, 147 trucks, 1 ambulance) and SUMO
   simulates them.

Record: `data/traffic/historical_central_silk_board/demand_conversion.json`.

---

## 3. The scenario: an ambulance caught in a queue

Two things decide whether the demo shows anything worth watching: how much traffic
the network is given, and when the ambulance sets off relative to the four-way's
450 s cycle. An ambulance that arrives first at the stop line has lost nothing to
the queue, and priority then only saves it the wait for the next green.

`scripts/historical_scenario_sweep.py` chooses both, on grids fixed in advance,
from **NORMAL runs only**. The scale grid is searched from the top down — every
value is a share of the observed peak hour, so a larger one is closer to what was
counted — and the departure grid from the earliest up. A pair is

* **valid** when the run has no teleports, the ambulance arrives, no signal state
  outside the programs appears, and the insertion backlog is at most 5% of the
  vehicles that entered;
* **usable** when, in addition, the ambulance's first halt within 400 m of the
  four-way stop line has at least **12 vehicles** between it and that line, and is
  at least **25 m** back from it — vehicles in front, and not already at the head.

The first usable pair wins. No EMS policy runs in the search and no travel time is
compared, so the scenario cannot have been tuned to flatter priority.

| k | depart | SUMO veh/h | routed | departed | teleports | backlog | ahead at first halt | halt m back | valid | usable |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|:--:|
| 0.40 | 600 | 9,053.6 | 9,849 | 2,905 | 7 | 387 | 61 | 333.6 | ✗ | ✗ |
| 0.40 | 700 | 9,053.6 | 9,849 | 4,025 | 24 | 1339 | 62 | 330.6 | ✗ | ✗ |
| 0.40 | 800 | 9,053.6 | 9,849 | 4,028 | 25 | 1328 | 3 | 6.0 | ✗ | ✗ |
| 0.40 | 900 | 9,053.6 | 9,849 | 4,792 | 39 | 1926 | 35 | 214.9 | ✗ | ✗ |
| 0.40 | 1000 | 9,053.6 | 9,849 | 4,635 | 53 | 1840 | 89 | 373.4 | ✗ | ✗ |
| 0.35 | 600 | 7,921.9 | 8,624 | 4,410 | 33 | 1362 | 43 | 241.7 | ✗ | ✗ |
| 0.35 | 700 | 7,921.9 | 8,624 | 6,098 | 80 | 2526 | 0 | 0.0 | ✗ | ✗ |
| 0.35 | 800 | 7,921.9 | 8,624 | 4,510 | 35 | 1331 | 32 | 192.0 | ✗ | ✗ |
| 0.35 | 900 | 7,921.9 | 8,624 | 3,967 | 28 | 874 | 33 | 349.4 | ✗ | ✗ |
| 0.35 | 1000 | 7,921.9 | 8,624 | 3,811 | 22 | 947 | 14 | 37.4 | ✗ | ✗ |
| 0.30 | 600 | 6,790.2 | 7,398 | 2,435 | 2 | 60 | 67 | 343.6 | ✗ | ✗ |
| 0.30 | 700 | 6,790.2 | 7,398 | 2,367 | 2 | 35 | 39 | 224.5 | ✗ | ✗ |
| 0.30 | 800 | 6,790.2 | 7,398 | 2,961 | 4 | 230 | 9 | 25.7 | ✗ | ✗ |
| 0.30 | 900 | 6,790.2 | 7,398 | 5,546 | 56 | 1852 | 0 | 0.0 | ✗ | ✗ |
| 0.30 | 1000 | 6,790.2 | 7,398 | 5,586 | 70 | 1812 | 0 | 0.0 | ✗ | ✗ |
| 0.25 | 600 | 5,658.5 | 6,160 | 3,202 | 7 | 186 | 80 | 397.4 | ✗ | ✗ |
| **0.25** | **700** | **5,658.5** | **6,160** | **1,969** | **0** | **16** | **37** | **219.2** | ✓ | ✓ **selected** |

Sixteen pairs were rejected before one was taken, and every rejection is the same
story: the denser levels make **bigger** queues — 61, 80, even 89 vehicles ahead —
and pay for them with teleports. A teleport is a vehicle vanishing off the road,
which is the one thing this demo must not show, so the rule refuses them however
good the queue looks. The pair it took, **k = 0.25 at 700 s**, is the first with
none: **37 vehicles** between the ambulance and the stop line, its first halt
**219 m** back from it, 0 teleports and a 0.8% insertion backlog.

Record: `data/processed/historical_demo/demand_scale_sweep.json`.

---

## 4. The four-way intersection

The research network models the at-grade Central Silk Board junction as four
independent approach signals: `3805003789`, `306594280`, `494271080` and
`2971089260`, each with a ~90%-green program and no conflict separation.
`scripts/build_historical_demo_network.py` builds a **demo-only network variant**,
`simulation/sumo/silk_board_v1_hdemo/`, in two netconvert passes, in which the
same four nodes share **one SUMO traffic light, `silk_board_4way`**, with 9
controlled links on 4 arms.

| Arm | Node | Approach edge | Links | Road |
|---|---|---|---|---|
| N | 2971089260 | 160331538#0 | 0 | (unnamed) |
| E | 494271080 | 380980308#2 | 6, 7, 8 | Outer Ring Road (westbound) — **the ambulance's approach** |
| S | 3805003789 | 1303068639#0 | 4, 5 | Srinagar–Kanyakumari Highway (NH-44, Hosur Road) |
| W | 306594280 | 464465163#0 | 1, 2, 3 | Outer Ring Road (eastbound) |

Split-phase, clockwise from N: each arm gets G 107.5 s, then y 3 s, then all-red
2 s. One arm is released at a time, so conflicting approaches are separated by
construction.

| Item | Value | Label |
|---|---|---|
| Cycle length | 450 s | OBSERVED (IJIRSET 2017) |
| Equal split, amber, all-red, phase order | as above | ESTIMATED |
| One controller for the four stop lines | `silk_board_4way` | ESTIMATED (modelling decision) |

The build verifies that edges, lanes, shapes and connections are unchanged, that
every other signal is unchanged, that no phase releases two arms, that each arm
gets G → y → all-red, and that the research network is byte-identical before and
after. Results: `network_provenance.json`.

---

## 5. The ambulance trip

The rule in `ems_sim/historical/trip.py` was fixed before any HISTORICAL_DEMO
simulation and refers to no travel time: route all 90 boundary pairs with
duarouter; keep those using a `silk_board_4way` movement; **drop those whose own
path no 6 m body could drive**; rank by **how much of the route shares ground with
a different road at the same level** (least first), then by red-exposed signals,
then length, then ids; take the first.

The two geometric steps are there because of what this network is made of, and
both are measured on the geometry the scene actually draws — the renderer's own
body placement, the validator's own overlap measure — rather than on a distance
threshold:

- **Geometry no vehicle could drive.** SUMO measures a following gap *along the
  lane path*. Where netconvert builds a connection that reverses direction inside
  a few metres — it does this at acute OSM intersections — a perfectly legal 8 m
  gap is 3 m of ground, and the replay draws the leader inside the ambulance. The
  previous trip had exactly one of these, a connection turning 159° in 4.88 m, and
  it produced exactly one drawn collision in the recording. The test walks the
  route and places a second body at every gap SUMO could leave, ahead and behind.
  **8 of the 41 four-way routes are rejected by it.**
- **Two roads on one piece of ground.** Parts of this extract have two at-grade
  roads digitised on the same ground: a service road and a slip road centimetres
  apart, and the Silk Board Flyover deck (way `1208112303`, which carries no
  `layer` or `bridge` tag in the extract) running along the Outer Ring Road it
  flies over. SUMO does not couple vehicles across such a pair, so both can occupy
  the same ground. **Every** route through the four-way has some of this, so it is
  minimised rather than forbidden — and the recording is then checked frame by
  frame (C30).

It selected **`312063814#2 → 1534247766#0`**, 2,220.8 m, red-exposed at the
four-way itself. On 1 metre of its length a vehicle on another road could be
drawn inside it (`-229976708#19`); no other candidate had less. Record:
`data/processed/historical_demo/trip_selection.json`.

The **departure** is the scenario sweep's (§3).

---

## 6. Priority that asks in advance

The research policies trigger on a **fixed distance** — EMS_NEXT at 250 m,
EMS_ROLLING at 700 m. A distance is not a lead time: 700 m is 36 s away at 70 km/h
and 175 s away at 14 km/h, and neither figure knows how long the controller needs
to reach a green for the ambulance, nor how long the queue in front takes to clear
once it does. Those policies are frozen and unchanged. HISTORICAL_DEMO runs its
own, `EMS_PREDICTIVE` (`simulation/ems_sim/historical/policy.py`):

```
request priority when   eta(distance, speed)  ≤  transition + clearance + margin

  eta         distance to the stop line ÷ the ambulance's own speed
              (floored at 4 m/s, so a stopped ambulance still has a finite estimate)
  transition  worst case time this controller needs to reach a phase serving the
              ambulance, from its own program: interphases run in full, a green
              may be cut at its 5 s minimum
  clearance   queue on the approach ÷ (lanes × saturation flow), from SUMO's own
              halting count
  margin      12 s, so the corridor is clear before arrival rather than at it
```

which is an activation distance that moves with the traffic:

```
activation_distance = max(speed, 4 m/s) × (transition + clearance + margin),  capped at 1,200 m
```

The transition term comes out of each controller's own program: **35 s** at the
four-way (its amber and all-red, then three arms each cut to their minimum green
with their interphases in full), 15 s at `GS_cluster_…`, 8 s at `11348815671`.

Everything the rule reads is observation — the ambulance's position and speed, and
SUMO's halting count. Like every policy here it only selects among the phases the
program already defines, so it cannot create a conflicting green, and it never
touches a vehicle. It holds a granted phase for up to 180 s (the research default
of 60 s would expire before an early request paid off) and then lets the program
resume.

One further difference, and the reason the record is legible: the policy keeps the
last known route index while the ambulance is inside a junction, where SUMO
reports none. The research policies read that gap as "off the route", release, and
re-request a step later — filling the transition record with flicker the signal
never showed.

### What we tried to make the traffic give way, and what happened

Signal priority clears the **signal**. It does not clear the **queue**: the
vehicles standing between the ambulance and the stop line still have to move. The
obvious next step is SUMO's own emergency-yielding mechanism, the **bluelight
device** (`MSDevice_Bluelight`) — drivers within a reaction distance of an
equipped vehicle get out of its way. It was implemented, enabled on the command
line only (`--device.bluelight.explicit`, so the routes file stays byte-identical
between the paired runs), and run on this scenario. Then it was measured, against
the NORMAL run's 532.0 s and 308.5 s of waiting:

| EMS configuration | travel | waiting | arrived |
|---|---:|---:|---|
| signal priority only | **255.0 s** | **24.5 s** | yes |
| + siren, 200 m reaction distance (SUMO's default) | — | 90.0 s | **no** |
| + siren, 200 m, with the sublane model at 0.8 m | — | 95.5 s | **no** |
| + siren, 30 m reaction distance | 815.0 s | 592.5 s | yes |

It does exactly what it promises, and that is the problem. In a saturated
three-lane corridor there is nowhere for the traffic to get out of the way *to*:
the vehicles in front stop to let the ambulance through, the ambulance cannot
drive through stopped vehicles that fill every lane, and neither side ever moves
again. In both 200 m runs the ambulance stood **at the same metre for 3,094 s**
(3,118 s with the sublane model) until the simulation ended, on an unsignalised
junction with an empty road in front of the queue. At 30 m it arrives — 283 s
*slower* than with no siren, and 53% slower than NORMAL.

The sublane model was tried because a rescue lane needs somewhere to go: at 0.8 m
of lateral resolution a car can sit to one side of its lane. It did not help, and
it is not used, so both paired runs keep the same lanes the research runs use.

So **the demo makes its case with signal priority alone**, and this is recorded in
the scene rather than papered over: the two runs differ in the signal policy and
in nothing else (C32), and the traffic in front of the ambulance moves because it
has a green — a real response in the recording, not a driver deciding to be
helpful. The mechanism stays in the code and stays reproducible:
`demo_run_options(..., siren=True)` brings the deadlock straight back.

The alternative would have been to make the cars move out of the way in the
renderer. That is the one thing this project will not do: it would be a picture of
a simulation that never happened.

---

## 7. What the two runs do (HISTORICAL_DEMO, seed 42 — not research)

| | NORMAL | EMS_PREDICTIVE |
|---|---:|---:|
| Ambulance travel time | **532.0 s** | **258.5 s** |
| Waiting time | 308.5 s | 25.0 s |
| Time standing still (measured from the recording) | 309.0 s | 26.0 s |
| Stops | 5 | 2 |
| Arrival (sim time) | 1,232.5 s | 959.0 s |
| Teleports / signal conflicts | 0 / 0 | 0 / 0 |
| **Saved (recorded difference)** | | **273.5 s (51.4%)** |

Both runs depart at 700 s, use the same demand, seed, network, trip and route,
inject no disturbance, and are given **exactly the same SUMO options** — the EMS
run buys no vehicle behaviour the baseline lacks (C32). Their recordings are
identical vehicle-for-vehicle until the policy first changes a signal (C08).

**The same two moments in both runs:**

| | NORMAL | EMS_PREDICTIVE |
|---|---|---|
| t = 860.0 s | standing still, **38 vehicles** between it and the stop line, 37 of them stationary | moving at 5.2 m/s, **4 vehicles** ahead, none stationary |
| t = 884.0 s | still standing, **41 vehicles** ahead, 40 of them stationary | crossing the stop line at 6.7 m/s, **0 ahead** |

**How EMS got there.**

- **793.5 s** — 548 m from the stop line at 11.4 m/s, the policy asks. Its own
  formula put the activation distance at **549.3 m** for that speed, so this is
  the first metre at which it could: *"estimated 48 s from the stop line at 548 m
  and 11.4 m/s, within the 48 s the signal needs (35 s transition + 1 s queue +
  12 s margin)"*.
- **820.0 s** — the ambulance's movement goes green, with **40 vehicles** still in
  front of it and the ambulance itself still 300 m short of the line.
- **820 → 884 s** — the queue discharges on that green, ahead of the ambulance
  rather than because of it: 40 vehicles at the green, 4 by 860 s, **0** when the
  ambulance reaches the line.
- **884.0 s** — it crosses on green (`rrrrGGrrr` — its own two links green, every
  conflicting movement red), 90.5 s after the request. Priority is released and
  the controller resumes its own phase order and programmed durations (C19).
- It never stands at that red at all: its longest halt at the four-way's red is
  **0.0 s** in both runs — in NORMAL because the queue stops it 219 m earlier.

**NORMAL, for contrast:** at 849.5 s the ambulance joins the back of a queue
**219 m** from the stop line with **37 vehicles** in front of it, 32 of them
already stopped. It stands still for **309 s** of its 532 s trip and arrives 273.5 s
after the EMS run.

**And the rest of the city stays where it was.** At the moment the EMS ambulance
crosses, **164** vehicles are standing still more than 250 m away from it, against
**184** in NORMAL at the same instant (C33). Priority cleared a corridor, not a
city.

Nothing here was tuned for the outcome: the trip came from a rule about the
network's geometry, the scale and departure from NORMAL-only runs, and the policy
from a formula fixed before it ran.

---

## 8. The 3D replay

- **SINGLE / COMPARE.** Left NORMAL, right EMS PRIORITY, one clock advanced by one
  pane. Each pane freezes at its own recorded arrival; the clock stops at the
  later one. The comparison panel shows NORMAL / EMS PRIORITY / SAVED from the run
  records, each once that run has arrived, and withholds SAVED if the loaded pair
  does not match the record the saving was computed from.
- **Playback.** PAUSE, 1×, 2×, 4×, 8× — visualisation speed only; the arrival
  freeze still applies.
- **Cameras.** Overview, Chase, Priority, Intersection, and AUTO. The intersection
  shot is computed **by the exporter** from the controller's own stop-line
  geometry — 130 m back down the ambulance's approach, 38 m to its right, 46 m up
  — so the queue and the junction are in one diagonal frame and the validator can
  check that line of sight. AUTO chases the ambulance, cuts to the four-way when
  the recorded request there begins, and returns to the chase after release; any
  manual choice switches it off.
- **What the scene draws, all from the recording:** three-aspect signal heads on
  mast arms, one per controlled link; a **stop bar carrying its approach's own
  aspect** (neutral when the heads of that approach disagree, because a split
  approach has no single colour); the "EMS PRIORITY ACTIVE · GREEN CORRIDOR ·
  Cross traffic STOPPED" banner, whose last clause appears only when the applied
  state has the ambulance's movement green and every other movement red; a
  translucent corridor along the route from the ambulance to the held signal; and
  delay markers that appear when their halt happens, name the recorded state that
  caused it, and say how many vehicles were ahead.
- **Buildings that stood in the road are not drawn.** OSM way `1508372362`
  (`building=construction`, `construction=train_station` — the metro station box)
  covers **596 m of live carriageway**, including the ambulance's own approach;
  two station footprints do the same on the flyover roads. Buildings have no
  vertical placement in this project, so every footprint is extruded from the
  ground and these were drawn *through* the road, filling the intersection view. A
  footprint covering at least 20 m of drivable lane centreline is left out —
  **16 of 2,500**, each listed with its overlap in `buildings.json`.
- **And the rest are trimmed off the carriageway.** Dropping whole footprints only
  deals with structures that cover a road. The common case is a wall digitised a
  metre or two into the traffic lane, which is a wall the traffic drives through:
  two of them stood in the lane the ambulance used, and it passed through both by
  over 2 m. So every remaining footprint is now cut back off the drivable surface
  — each lane's own shape at its own width plus a 0.75 m margin, plus the junction
  shapes. **915 of 2,484** footprints lost some area, 15,342 m² in total, and none
  lost all of it: the buildings stay in the scene, only the part standing on the
  road goes. The margin is vehicle geometry, not taste: SUMO puts a vehicle on the
  lane centreline, the widest body here (a 2.5 m bus) reaches 1.25 m out inside a
  3.2 m lane, and the margin covers a long body's corner swinging out on a
  junction curve. Report: `buildings.json` → `carriageway_clip`.
- **Flyover.** Structure ends are **ramped** over 120 m (ESTIMATED) so no deck end
  hangs in mid-air; deck depth 1.1 m with soffit, fascia and parapets; round piers
  with caps, only where the underside clears 2.6 m and never inside a ground
  carriageway. Vehicle elevation follows the same profile using SUMO's lane
  position.
- **Recording rate.** HISTORICAL_DEMO records trajectories every 1.0 s rather than
  0.5 s: at this demand a 0.5 s recording is ~100 MB per scene and the comparison
  parses two. The rate is an output setting — it changes nothing in the simulation
  — and the renderer already interpolates between recorded samples.

The earlier DEMO scene still loads at `?scene=demo`.

---

## 9. Validation

`python scripts/validate_historical_demo.py` runs 33 checks, including a fresh
SUMO rerun, and writes `data/processed/historical_demo/validation.json`.
**All 33 pass.** Alongside them: 570 pytest, 57 vitest, `tsc -b` and ruff clean,
and `validate_scene_coords.py` clean on both scenes (0 failures over 211,248 and
428,777 vehicle samples — including every frame of the ambulance checked against
SUMO's own record of it).

Four results worth quoting.

- **C29 / C30 — nothing is drawn inside anything.** Both scenes are re-measured as
  they are drawn: a body per vehicle, placed from the recorded position, the
  recorded heading and the simulated dimensions, tested against the 2,484 drawn
  building footprints and against every other vehicle in the frame. **0 building
  intersections and 0 vehicle overlaps in both runs** (320 and 593 frames), at a
  5 cm tolerance that
  is numerical and nothing else — the exporter rounds positions to 1 cm and
  headings to 0.1°, which at the far corner of the longest body in the scene is
  half a centimetre.
- **C26 — the corridor cleared ahead of the ambulance, not because of it.** 40
  vehicles stood between it and the stop line when the green came at 820.0 s, and
  0 when it crossed at 884.0 s.
- **C33 — and only the corridor.** 164 vehicles were standing still more than
  250 m from the ambulance when it crossed, against 184 in NORMAL at that instant.
- **C20 — the rerun reproduced the exported EMS run exactly**: arrival, travel
  time, waiting, stops, every policy transition and every signal timeline.

| Id | Check |
|---|---|
| C01 | Sources documented |
| C02 | Observed labels |
| C03 | No unit confusion |
| C04 | Conversion chain and exact car share |
| C05 | UI labels OBSERVED vs SIMULATED, not GPS |
| C06 | Separate namespace |
| C07 | Identical initial conditions, departure included |
| C08 | Identical FCD until the policy acts |
| C09 | Comparison is the recorded difference |
| C10 | Both runs stop at arrival |
| C11 | The ambulance's four-way movement is exposed to red |
| C12 | The four-way is a real SUMO TLS |
| C13 | Lamp states are program states |
| C14 | Never two arms released |
| C15 | No green → red without amber |
| C16 | NORMAL held on its way to the four-way; EMS crossed on green, held less |
| C17 | Priority changed the applied state only after its request |
| C18 | Conflicting movements red while shown active |
| C19 | Return to program order and durations |
| C20 | Deterministic replay |
| C21 | Flyover without floating ends |
| C22 | Frozen research byte-identical |
| C23 | Controls present |
| C24 | NORMAL is caught behind a queue, not leading it |
| C25 | Priority requested well before the four-way |
| C26 | The queue discharged before the ambulance arrived |
| C27 | Intersection camera has a clear line of sight; no footprint on the road |
| C28 | No teleports, no signal states from outside the programs |
| C29 | The ambulance is never drawn inside a building, in either run |
| C30 | The ambulance is never drawn inside another vehicle, in either run |
| C31 | Every vehicle is drawn at the length and width SUMO simulated it with |
| C32 | The EMS run buys no SUMO behaviour the NORMAL run does not have |
| C33 | Priority clears the ambulance's corridor, not the rest of the network |

Alongside: pytest, vitest, tsc, ruff, and `validate_scene_coords.py` for both
scenes (round trip, georeference, scale, heading, continuity, elevation).

---

## 10. Limitations

- **Time mismatch.** 2014–15 counts replayed on a present-day OSM network that
  includes structures which did not exist then.
- **k = 0.25.** The replay uses 25% of the observed peak hour. Every denser level
  the grid offered made a longer queue and was rejected for teleporting vehicles —
  sixteen pairs before this one.
- **Not observed:** turning proportions, and the non-car split.
- **Car share scope.** The 53% may describe one movement rather than the whole
  junction, and it comes from a different survey than the volume.
- **Signal timing.** Everything except the cycle length is ESTIMATED. The 600 s
  teleport threshold exists because of the 450 s cycle.
- **Departure chosen for the scenario.** 700 s was picked because it puts the
  ambulance in a queue — from NORMAL runs, never from an EMS result, but it is a
  choice and it is why the demo looks like this.
- **No traffic gets out of the ambulance's way.** SUMO's own mechanism for it
  deadlocks this corridor (§6), so the only thing working for the ambulance is the
  signal. A real siren does more than that, and this demo does not show it.
- **The route is chosen partly for its geometry.** Two of the rule's five steps
  are about whether the network can be *drawn* without vehicles inside each other
  (§5). That is a legitimate constraint on a visualisation and it is not one a
  traffic study would impose.
- **One seed, one trip.** HISTORICAL_DEMO is a demonstration, not a study.
- **Download size.** The NORMAL scene's trajectories are ~15 MB and the EMS
  scene's ~7 MB; the paired run loads only when COMPARE is first opened.

## 11. Reproduce

```bash
python scripts/build_historical_demo_network.py
python scripts/select_historical_demo_trip.py
python scripts/historical_scenario_sweep.py
python scripts/export_scene.py --historical --policy NORMAL --out compare
python scripts/export_scene.py --historical --policy EMS_PREDICTIVE
python scripts/validate_scene_coords.py --scene-dir frontend/public/scene/historical --out data/processed/historical_demo/scene_validation_EMS_PREDICTIVE.json
python scripts/validate_scene_coords.py --scene-dir frontend/public/scene/historical/compare --out data/processed/historical_demo/scene_validation_NORMAL.json
python scripts/validate_historical_demo.py
```
