# Phase 7: the 3D digital twin

How a committed SUMO run becomes an interactive 3D scene, what in that scene is
real, and what is reconstructed.

> **The scene renders a simulation.** Vehicle positions, speeds, signal states
> and travel times are SUMO output under estimated demand and generated signal
> timings. Nothing in it is a measurement of real traffic or of any real
> ambulance journey in Bengaluru. The road network and building footprints come
> from OpenStreetMap; the heights and elevations you can see do not.

Companions: [`FINAL_RND_REPORT.md`](FINAL_RND_REPORT.md) (the research this
visualises) · [`3D_READINESS.md`](3D_READINESS.md) (the gate) ·
[`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 1. Data flow

```
OpenStreetMap extract  ─┐
                        ├─► netconvert ─► silk_board_v1.net.xml ─┐
committed .netccfg     ─┘                                        │
                                                                 ├─► export_scene.py ─► frontend/public/scene/*.json
demand config + seed ─► duarouter ─► routes ─► SUMO (TraCI) ─────┘                          │
                                       │                                                    ▼
                                       └─► fcd.xml (per-step vehicle state)          React + R3F renderer
```

The frontend is a **renderer, not a simulator**. It holds no car-following
model, no signal controller and no router. Every position, heading and signal
state it draws was produced by SUMO and transformed exactly once on the way out.

### The FCD run

`scripts/export_scene.py` runs SUMO once with `--fcd-output`, using the **same
network, demand, seed and policy** as the committed counterfactual result. It is
a separate run writing to its own directory; it reproduces the published result
rather than replacing it. The seed-42 `two_signal` NORMAL export reproduces the
committed ambulance travel time of 184.5 s exactly.

FCD is off by default in `SumoRunOptions` because it is large — 282 MB for one
3,900 s run at 0.5 s — and no analysis needs it. Requested columns are
`x,y,z,angle,speed,pos,lane,type,slope`; `lane` and `type` are not FCD defaults
and both are needed, `lane` to place a vehicle on the correct carriageway of a
grade-separated junction and `type` to size it.

### Exported payload

| File | Contents | Size |
|---|---|---|
| `manifest.json` | Scenario, seed, policy, network hash, ambulance summary, transform, provenance | 5 KB |
| `network.json` | 7,034 lane polylines, 645 junctions, 231 generated kerbs | 1.9 MB |
| `signals.json` | 8 traffic lights, 48 controlled links, phase programs | 8.5 KB |
| `buildings.json` | 2,500 OSM footprints with heights | 437 KB |
| `trajectories.json` | 721 frames, 468 vehicles, 132,778 samples | 4.7 MB |

Trajectories are **frame-major with interned ids**: parallel flat arrays of
`x/y/z/angle/speed/lane` per timestep, referencing string tables. A renderer
needs "every vehicle at time t" 60 times a second and "one vehicle over all t"
only on selection, so frame-major makes the hot path an array index. The same
data as an array of objects is roughly four times larger.

## 2. Coordinate systems

Four frames, defined once in `simulation/ems_sim/viz/coords.py` and mirrored for
the renderer in `frontend/src/scene/lib/coords.ts`.

| Frame | Units | Axes | Source |
|---|---|---|---|
| WGS84 | degrees | lon east+, lat north+ | OSM |
| UTM 43N | metres | easting, northing | `projParameter` in the net's `<location>` |
| SUMO network | metres | x east+, **y north+** | UTM + `netOffset`, also from `<location>` |
| Scene (Three.js) | metres | x east+, **y up**, **z south+** | this project |

```
scene.x = (sumo.x - origin.x) * scale
scene.y = elevation
scene.z = -(sumo.y - origin.y) * scale
```

**No hardcoded offsets.** `netOffset` (`-782235.23, -1428304.73`) and
`projParameter` (`+proj=utm +zone=43 +ellps=WGS84 +datum=WGS84`) are read from
the network file. The scene origin is **computed** as the centre of the bounding
box of the network's own lane geometry — not `convBoundary`, which describes the
larger area netconvert converted before clipping and would leave the study area
off to one side. The origin is carried in `manifest.json`, so the renderer never
guesses it.

Subtracting an origin is not cosmetic: raw UTM eastings here are ~7.8 × 10⁵, and
32-bit floats in a GPU vertex buffer lose sub-centimetre precision at that
magnitude.

`scale` is 1.0. The scene is in metres, so a 4.5 m car is 4.5 scene units long.

### Heading

SUMO reports heading in degrees clockwise from north. In the scene north is −Z
and east is +X, so direction of travel is `(sin a, 0, −cos a)`. A rotation of θ
about +Y maps a mesh's −Z axis to `(−sin θ, 0, −cos θ)`, so **θ = −radians(a)**
reproduces it exactly, and **meshes are modelled nose-forward along −Z**.

### Elevation — the one substantive reconstruction

**The SUMO network carries no z values at all.** Every one of its 7,034 lane
shapes is 2D; this was verified directly, not assumed. Rendering that literally
would lay the Silk Board flyover flat on the ground and let vehicles drive
through each other.

Elevation is therefore reconstructed from OSM's `layer` tag at
**6.0 m per layer step** (`LAYER_HEIGHT_M`), read from the committed
`elevated_and_underground.geojson` (24 grade-separated ways). `layer` is an
ordinal — it says what is above what, not by how much — so this recovers the
**ordering** of structures, not their heights.

**This is `ESTIMATED_DATA` and is labelled as such at every point of use**: in
the exported manifest, in the transform metadata, in the HUD's provenance panel,
and next to the elevation value in the selection panel. No height in this scene
may be read as a measurement.

Internal (junction) edges carry no OSM way, so a naive lookup returns ground
level for all of them — which would drop a vehicle to the ground every time it
crossed a junction on the flyover. `edge_layer()` resolves them from the maximum
layer of the roads meeting at that node instead.

## 3. Road network generation

Lane centrelines from the network are extruded into ribbons of the lane's own
width and **merged into a handful of buffers**; 7,034 individual meshes would
dominate the frame budget for geometry that never moves.

Grouped by structure so each can be materialised and, where needed, given
supporting geometry:

- **surface** — asphalt at ground level
- **bridge / elevated** — deck, plus a soffit and pylons at ~25 m spacing so it
  reads as a structure rather than a stripe of asphalt hanging in the air. Pylon
  spacing is a rendering choice; the deck's plan position comes from the network
  and its height ordering from the OSM layer tag.
- **internal** — junction connectors, drawn slightly proud so turns are visible
- **junction surfaces** — filled polygons from `node.getShape()`

**Lane markings** are derived, not invented: a divider is placed on the boundary
between adjacent lanes of the same edge (half a lane width off the lower lane's
centreline), and an edge line along the outer lane. **Kerbs** are offset strips
alongside major-class edges only — the network was converted with
`--keep-edges.by-vclass passenger` and contains **no footway geometry at all**,
so these are `ESTIMATED_DATA` and are a visual edge to the carriageway, not a
representation of any real pavement.

## 4. Buildings and environment

**Footprints are real** — 3,179 OSM building ways, `PUBLICLY_SOURCED_DATA`, used
unmodified.

**Heights are almost entirely not.** Of those 3,179 ways, **3 carry a `height`
tag and 15 carry `building:levels`**. The other ~2,480 exported buildings get a
height from a deterministic rule on footprint area and building type:

```
levels = clamp(round(2 + log2(area / 60)) + type_bias, 2, 14)
height = levels * 3.2 m
```

Area is a weak but genuine correlate of height in dense urban fabric, and the log
makes the response gentle — doubling the footprint adds one storey. **There is no
randomisation.** Jitter would look more natural and would be a fabricated detail
in a project whose entire discipline is that its outputs say where they came
from. Buildings render in a flat desaturated material so they read as context
massing rather than as surveyed geometry.

## 5. Vehicles

One `InstancedMesh` per vType — seven draw calls for ~470 vehicles. Dimensions
mirror the SUMO vTypes exactly, so a 12 m bus is 12 m: a vehicle drawn longer
than it is simulated would overlap the gap the car-following model actually left.

Between two recorded samples the transform is linearly interpolated so 2 Hz data
reads smoothly at 60 Hz. This is a **display convenience, bounded by the samples
either side**:

- it never runs past the last recorded frame (`frameFraction` clamps; nothing is
  extrapolated),
- a vehicle absent from the next frame is held at its last recorded position, not
  slid toward another vehicle's slot,
- a vehicle absent from a frame is scaled to zero, not parked at the origin,
- headings interpolate along the **shortest arc**, the only reading consistent
  with two recorded angles — linear interpolation would spin a vehicle 358° the
  wrong way when it crosses north.

**Any frame drawn between two samples is a display artefact, not a simulated
state**, and the exported trajectory says so in its own `note` field.

## 6. Ambulance

Drawn separately from the instanced traffic **only** so it can have a distinct
silhouette, a beacon and a route ribbon. It reads the same interned vehicle out
of the same recorded frames through the same interpolation. There is deliberately
no fallback path: if the recording does not contain it, nothing is drawn. An
ambulance that kept moving when the record ran out would be a scripted object.

The beacon flashes on **wall-clock** time, not simulation time, because SUMO
models no siren — it is a rendering cue with no counterpart in the simulation.
The HUD states that under NORMAL the ambulance has no siren, no speed bonus and
no right-of-way override.

## 7. Traffic signals

One head per controlled link, placed at the **stop line of its incoming lane**
with the heading of that lane's final segment, so it faces oncoming traffic. The
network has no signal-head geometry, so placement is generated — but each head is
bound to a real SUMO link index.

Colour comes from the traffic light's own program at the current simulation time,
using SUMO's convention unchanged: index *i* of the state string is link index
*i*; `G`/`g` green, `y`/`Y` yellow, `r`/`R` red, `o`/`O` off.

**Geographically close traffic lights are not merged.** The four Silk Board
signals are separate `tlLogic` entries and are drawn as separate signals. Merging
them would show one controller where the simulation has four, and every statement
about which junction delayed the ambulance would stop meaning anything.

Signal **programs** are netconvert-generated `ESTIMATED_DATA`, not observed
Bengaluru timings, and the signal panel says so.

## 8. Playback and synchronisation

There is exactly **one clock**, advanced in one place (`Scene.tsx`), and
everything reads it: vehicles, signals, HUD, timeline. This is a correctness
requirement. If signals advanced on their own timer while vehicles advanced on
another, the scene could show a vehicle crossing on red that never crossed on red
in SUMO — a picture contradicting the data it claims to show.

Implemented as an external store with `useSyncExternalStore`, so a 60 Hz tick
does not re-render the React tree; in-canvas components read it imperatively in
their frame loop.

Controls: play/pause, reset, timeline scrub, speed (0.25×–8×), and **step
forward/back by whole recorded samples**, so a stepped frame is always a real
one. Playback wraps at the end of the window rather than running past it — there
is no recorded state out there.

## 9. Numerical validation

`scripts/validate_scene_coords.py`. Six independent checks, because a 3D scene
fails silently: a mirrored axis or a dropped elevation still looks like a city.

| Check | What it proves | Result |
|---|---|---|
| Round trip | Scene coordinates convert back to the SUMO values they came from | **492 checks, 0 fail, max error 0.0000 m** |
| Georeference | A lon/lat maps to the SUMO coordinate sumolib independently computes | **12 checks, 0 fail** |
| Scale | Vehicle-pair distances preserved in metres | **32 checks, 0 fail** |
| Heading | Angles round-trip, and vehicles move where they point | **40 checks, 0 fail** |
| Continuity | No vehicle moves further between samples than its speed allows | **0 violations in 132,778 samples** |
| Elevation | Every vehicle's height matches its lane's reconstructed elevation | **0 mismatches** |

**577 checks, 0 failures.** Output: `data/processed/silk_board_v1/scene_validation.json`,
with per-row vehicle id, timestep, source position, transformed position,
expected position, per-axis error, total error, tolerance and PASS/FAIL.

The heading tolerance is **derived, not picked**. Its premise — that the chord
between two samples approximates the heading — only holds while forward motion
dominates lateral motion, and a lane-changing vehicle points along the lane while
sliding sideways. One motorcycle failed the first run for exactly that reason. The
allowance is now the angle that SUMO's own default lateral speed subtends over the
observed chord, plus a margin, and only chords over 3 m are compared. A mirrored
axis fails this by ~180° and is nowhere near any of it.

## 10. Provenance in the UI

The project's discipline is that a measured number and an assumed one must never
look alike, and a 3D scene is the easiest place in the whole system to lose that:
everything rendered at the same fidelity reads as equally real. So the HUD carries
a permanent provenance legend, and every panel that shows a value shows its data
class beside it.

| Class | In this scene |
|---|---|
| `VERIFIED_REAL_DATA` | **Nothing.** Shown greyed out in the legend, to make the absence visible |
| `PUBLICLY_SOURCED_DATA` | Road geometry, building footprints (OSM) |
| `ESTIMATED_DATA` | Road elevation, building heights, kerbs, signal programs, the trip config |
| `SIMULATED_DATA` | Vehicle positions, speeds, headings, signal states, travel times (SUMO) |

## 11. Limitations

1. **Road elevation is reconstructed, not surveyed.** 6 m per OSM layer step,
   uniform. Ordering is right; heights are not measurements.
2. **Building heights are ~99% estimated** (3 of 3,179 ways carry a height tag).
3. **Kerbs are generated.** The network has no footway geometry.
4. **No sidewalk, crossing, vegetation, streetlight or sign geometry exists** in
   the source, so none is drawn. Inventing street furniture would add exactly the
   kind of unlabelled detail this project avoids.
5. **Trajectories are clipped** to 540–900 s, bracketing the ambulance trip. This
   is a file-size decision and never alters a value inside the window.
6. **Only NORMAL is exported so far.** The renderer is built to take more.
7. **Signal programs are netconvert-generated**, so the signal timing on screen
   is an assumption, not Bengaluru's.
8. **Interpolation between 0.5 s samples is a display artefact**, bounded by the
   recorded samples but not itself simulated.
9. Junction surfaces use fan triangulation, which shows a small artefact on
   concave shapes. It never moves a lane centreline.

## 11a. Delay-event markers — the first research overlay

The scene marks **where the ambulance was actually stopped, and by what**, built
only from recorded data. `signal_wait_events` in the manifest is the corrected
detector's own output: simulation time, distance to the stop line, the state of
the ambulance's controlled links at that instant, and whether that state was red.

The marker's **position is not in the event** — the event has no coordinates. It
is looked up in the trajectory recording at the event's own timestamp, so the
marker stands exactly where SUMO had the ambulance when the halt was detected.

Red and amber are kept distinct, and that distinction is the entire point of the
detector correction (report section 12): a halt at a red is time a priority
policy could recover; a halt beside a signal that is green for the ambulance's
movement is a queue the policy cannot touch. Colouring them alike would put back
the conflation the correction removed.

On the seed-42 `two_signal` NORMAL export this produces one marker —
**"HELD AT RED", t = 671.0 s, 8.2 m from the `GS_cluster` stop line** — which is
the single event the entire 12.5 s headline rests on. Scrubbing to that time
shows the ambulance stationary at 0.04 m/s on edge `1393474724#1`, with the HUD
reporting the committed run's own 5.5 s waiting time and 184.5 s travel time.

## 12. Extension points for the research visualisation

The renderer is deliberately separated from what it renders, so the comparisons
the research needs can be added without rewriting it:

- **Scene payload is per (trip, seed, policy)**, chosen by `export_scene.py`
  arguments. Exporting `EMS_NEXT` alongside `NORMAL` is a second invocation.
- **The clock is external and shared.** Two scenes can be driven from one store
  for paired replay, and nothing in the components assumes a single canvas.
  **True split-screen needs more than this**: selection and camera state are
  currently single-valued in the store, so two panes would share a selection.
  Keying `SimState` per pane while keeping the clock shared is the prerequisite,
  and it is not done.
- **Vehicles resolve by interned id**, so the same vehicle can be located across
  two policy runs of the same seed to show where the paths diverged.
- **Signal state is a pure function of `(program, time)`** (`phaseAt`), so a
  baseline and a counterfactual signal timeline can be shown side by side.
- **`manifest.route_traffic_lights`** already carries per-signal green fraction,
  actionability and the ambulance's link indices — the inputs to an intersection
  attribution overlay.
- The HUD reads everything from the manifest, so a policy comparison panel is
  additive.

**None of those comparisons are implemented, and none of their results are
fabricated in the current scene.**

---

## 13. Independent review, and what was done about it

An independent reviewer (senior R3F engineer / traffic-visualisation engineer /
digital-twin designer / UX / research-visualisation) reviewed the code and
exported artifacts. Its findings and the decisions taken on each:

### Implemented

| Finding | Why it was justified | Change |
|---|---|---|
| **Route ribbon drew lane 0 of each route edge**, not the ambulance's own lane | A lane-level claim the recording does not support. On a multi-lane approach the ribbon diverged from the vehicle it describes | Ribbon is now built from the ambulance's **own recorded samples**, so it is by construction where SUMO put it (`Ambulance.tsx`) |
| **Signal replay assumed `type="static"`, `offset="0"`** | Latent, but the project's rule is that nothing may invent signal state. An actuated program cannot be reconstructed from a phase list at all | Exporter now records `type`, `offset` and `replayable`; `phaseAt` takes the offset; a non-static program is drawn **dark** and flagged in the HUD rather than guessed (`network_export.py`, `TrafficSignals.tsx`, `Hud.tsx`) |
| Ground plane extent hardcoded at 6000 | An unexplained constant, which this project does not allow | Derived from the network's own lane bounding box (`Scene.tsx`) |
| `indexOf` scans per frame in three components | O(n) over ~470 vehicles every frame, and a ~300-entry `Map` allocated per frame in `Vehicles` | One `Int32Array` per frame mapping vehicle → slot, built once (~1.3 MB), O(1) everywhere (`lib/frameIndex.ts`) |
| No geometry disposal | A real GPU leak the moment scenes are mounted/unmounted for comparison views | Disposal on unmount for road, junction, soffit, building and vehicle geometries |
| Sequential fetches of ~7 MB | Pure load-time waste | `Promise.all` after the manifest, plus an `AbortController` on cleanup |
| `onPointerMissed` left the camera locked | A hidden mode: HUD showed nothing selected while the camera still followed | Clears `followVehicle` too |
| Stale `''`-vs-`null` comment in the store | Would mislead the next contributor | Corrected |
| All buildings one flat grey | Monotone at close range | Three tones keyed to the OSM `building` tag — real data, not randomised shading |
| Beacon cue had no provenance badge | The one rendering cue in the scene without one, inconsistent with the project's own discipline | HUD now states the beacon is wall-clock and has no simulation counterpart |

### Not implemented, and why

- **Factory-ize the store per scene id** (for split-screen). Correct that the
  current singleton blocks independent selection/camera per pane, and the
  reviewer was right that section 12 was over-confident about it. But refactoring
  now would churn working code with no second pane to validate against. The
  claim in section 12 has been corrected instead: the clock is genuinely
  shareable, and single-pane paired replay is additive, but **true split-screen
  needs the store keyed per pane first**.
- **Junction fan-triangulation artefact** on concave shapes — cosmetic, and it
  never moves a lane centreline.
- **Street furniture** — deliberate. No such geometry exists in the source, and
  inventing it is exactly the unlabelled detail this project avoids.
- **Playback bar crowding** — a real forecast, not a current defect. Noted for
  when the comparison panel lands.

### Found by the implementer during review follow-up

The reviewer read `export_traffic_lights` and reasonably assumed it worked. It
did not. `sumolib.net.readNet` does **not** load traffic-light programs unless
`withPrograms=True` is passed, and without it `getPrograms()` returns nothing:
the network parses, all 8 traffic lights are found, all 48 controlled links are
placed, and every signal renders permanently dark. **Nothing errors.**

Fixed, and the exporter now **raises** if a traffic light exports with no
program, because a signal drawn dark for a whole run silently misrepresents the
simulation. All 8 now export `static`, offset 0, 3/4/6 phases, 90 s cycle —
matching the audit in `FINAL_RND_REPORT.md` section 9.

### End-to-end verification after the fixes

At t = 671.0 s the scene reconstructs `GS_cluster` state `rrrrGyyyyyyyy`. The
ambulance's controlled link at that signal is index 3, so the state it faces is
`r`. The corrected detector's recorded `link_states` for that same halt is `"r"`,
with `stopped_by_signal: true`.

**The renderer's independent signal reconstruction agrees with SUMO's recorded
TraCI observation at the exact instant the 12.5 s research finding rests on.**

---

## 14. Vehicle assets

**External sourcing was attempted first and failed.** Network access works — a
Khronos sample GLB downloaded successfully as a capability test. The Khronos
glTF-Sample-Assets index (150 models) contains five vehicle-ish entries:
`CarConcept`, `CesiumMilkTruck`, `ToyCar`, `ClearCoatCarPaint`, `CarbonFibre`.
All are rendering-feature showcases, and the set contains **no motorcycle, no
auto-rickshaw, no bus and no ambulance**. Putting a stylised milk truck and a toy
car into a Bengaluru traffic scene would be a recognisable, wrong claim about the
fleet.

So the seven types are **authored in code** (`lib/vehicleGeometry.ts`) as
silhouettes that are identifiable at traffic distance: a car with bonnet, glazed
cabin and boot; a two-wheeler *with a rider*, which is what makes it read as one;
a three-wheeled auto-rickshaw with a canopy; a bus with banded glazing and three
axles; a truck with a separate cab and load body; a van as one continuous box; an
ambulance with livery band and roof light bar.

**Length and width come from the SUMO vType**, so a vehicle occupies the space
the simulation gave it. Heights, proportions and colour are authored and carry no
data.

`lib/assetRegistry.ts` builds **one geometry per type**, shared by every instance
— ~470 vehicles cost seven geometries and seven draw calls. A licensed GLB drops
in per type by setting `glbUrl` in the registry; **the fallback is never silent**,
and `assetReport()` surfaces the source per type in the HUD.

Full provenance, including what was searched and why it was rejected:
`data/provenance/3d_assets.json`.

## 15. Disturbance

`ems_sim/disturbance/incident.py`. A declared, hashed lane blockage applied
through TraCI: for its window the lane is closed to all vClasses and its speed
limit dropped, then both are restored **to the values that were read at apply
time**, not to guessed defaults.

**It touches the road, never a vehicle.** No vehicle is moved, stopped, rerouted
or re-timed, and a test asserts the controller has no vehicle API in reach. Every
queue is produced by SUMO's own car-following and lane-change models. `emergency`
is among the blocked classes deliberately — exempting the ambulance would hand it
a private lane, and the travel time would then measure the exemption.

The config hash covers every field that changes the incident's effect, and the
integrity check compares it across a seed's four policy runs, so the disturbance
is provably part of the shared scenario rather than a variable.

### The location rule, and the one time it changed

First rule: *the longest route edge upstream of the actionable signal* →
`92196679#0` (964 m). Simulating it produced **no congestion at all**, and
measurement showed why: that edge carries **26 vehicles in 360 s**, about four a
minute. A lane closure on a road that empty cannot form a queue.

Rule now: *the approach edge of the actionable traffic light on the ambulance
route* → `1393474724#1`. It is the one place where a capacity reduction interacts
with the signal the study is about, and where the delay attribution already
lives. **The rule changed on the flow measurement, not on any effect on ambulance
travel time** — that was not consulted.

### The honest outcome

The incident works: it applies and clears on schedule, and produces genuine
queues — **202 m maximum network-wide, 48.6 m on the ambulance's route**.

**It does not measurably delay this ambulance.** Seed 42 returns 184.5 s with the
incident and 184.5 s without it. The reason was measured rather than guessed: all
three approaches to `GS_cluster` carry only **20–30 vehicles per 360 s** at demand
scale 0.5. This junction is lightly loaded, and no blockage near it can congest
it.

That is preserved as a finding. Inflating demand until the disturbance bit would
have meant abandoning a calibration that Phase 4 established as the maximum
before gridlock, in order to manufacture a more dramatic scenario — which is the
practice this project exists to avoid.

## 16. Comparison and demo modes

**Comparison** (`CompareView.tsx`) renders two exported runs of the same scenario
side by side. Only the left pane advances the clock; the right reads it. They
cannot drift, and a drifted comparison would compare different moments.

**Research demo** (`hud/DemoMode.tsx`) is a guided reading of one recorded run,
not a separate animation. Each chapter is anchored to a time **taken from the
run's own record** — the incident window, the ambulance's departure, the recorded
halt, the policy's first transition — so selecting a chapter seeks the shared
clock.

Chapters whose evidence is absent from the run are **struck through and not
seekable**. On the current scenario that matters: the disturbance forms real
queues but does not delay this ambulance, so a demo that narrated "ambulance
delayed by congestion" would be describing something that did not happen.

## 17. EMS status panel

Replays the policy's **recorded state transitions**, showing whichever transition
occurred at or before the current time. State is never inferred from where the
ambulance is: `PRIORITY_ACTIVE` means priority was granted, not that the
ambulance was nearby. Under NORMAL the panel says no policy was acting, rather
than showing an idle "NORMAL" that could be mistaken for a policy running and
doing nothing.
