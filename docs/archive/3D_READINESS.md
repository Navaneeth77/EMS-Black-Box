# 3D readiness gate

Whether the simulation work is in a state that justifies building the
Three.js / React Three Fiber digital twin, and exactly what that frontend may
consume.

Full evidence: [`FINAL_RND_REPORT.md`](FINAL_RND_REPORT.md).

---

# READY: YES

The R&D gate is satisfied. No blocking issues remain.

This is a judgement that the **simulation and its claims are sound enough to
visualise**, not that the model is validated against Bengaluru. It is not, it
does not claim to be, and the visualisation must not imply otherwise.

---

## 1. Gate checks

| Check | Status | Evidence |
|---|---|---|
| Network is stable | **pass** | One committed `.net.xml`, SHA-256 recorded in every result; 1,515 edges, 8 traffic lights; reviewed by `review_sumo_network.py` |
| SUMO runs reproducibly | **pass** | `verify_determinism.py`; independently confirmed here — seed 42 and the fixed-schedule control both re-ran and reproduced prior results exactly |
| Selected scenario is defined | **pass** | `two_signal`: `1311812959#0` → `1196514116#0`, depart 600 s, committed as `TWO_SIGNAL_AMBULANCE_TRIP` |
| Ambulance route is valid | **pass** | 11 edges, 2,435 m, identical across all 20 runs and all 4 sensitivity variants; no elevated/underground edges; does not use `way/1351994264` |
| Signal control is valid | **pass** | 0 conflicts, 0 out-of-program states, 220 legal transitions across the final set |
| Detector is corrected | **pass** | Two defects fixed and validated: fires on a red-stop, correctly declines a green-signal halt (report §13) |
| Counterfactual comparisons reproducible | **pass** | Paired identity verified in 5/5 seeds; mismatched pairs raise rather than report |
| Safety checks pass | **pass** | `analyse_final_experiments.py` → `passed: true`, 20 runs |
| Provenance documented | **pass** | One provenance record per dataset, each with data class, `produced_by`, seed, tool versions, limitations |
| Assumptions documented | **pass** | Report §5–§9, §16 |
| Limitations documented | **pass** | Report §20 |
| Results justify visualisation | **pass** | A stable, mechanistically explained ambulance result across 5 seeds, and an explicitly quarantined traffic metric |

## 2. What the 3D frontend may safely consume

### Geometry — available now

| Artifact | Contents |
|---|---|
| `simulation/sumo/silk_board_v1/silk_board_v1.net.xml` | Edges, lanes, shapes, junctions, projection in `<location>` |
| `data/processed/silk_board_v1/junctions.geojson`, `intersections.geojson` | Junction geometry |
| `data/processed/silk_board_v1/elevated_and_underground.geojson` | 24 grade-separated ways with `layer_effective` — **required** for correct flyover/underpass rendering |
| `data/processed/silk_board_v1/network.gpkg` | Full network geometry |

Coordinate transform ([`ARCHITECTURE.md`](../ARCHITECTURE.md)):
`x = sumo.x - ox`, `z = -(sumo.y - oy)`, `y = elevation`. Y is up in Three.js and
north in SUMO — this axis swap is the single easiest thing to get wrong.

### Simulation results — available now

| Artifact | Contents |
|---|---|
| `counterfactual/two_signal_seed4{2..6}_{NORMAL,EMS_*}.json` | Per-run ambulance trip, per-edge times, route, signal-wait events with red/green classification, policy transitions, conflicts, teleports |
| `counterfactual/two_signal_seed4{2..6}_comparisons.json` | Paired differences and intersection attribution |
| `ems/route_traffic_lights_*.json` | Which signals are on the route, their links, green fractions, actionability |
| `final_analysis.json` | Multi-seed aggregate, safety validation, uncertainty |

Every policy state transition carries simulation time, the ambulance's
edge/lane/position/speed, distance to the signal, the signal state and phase
before and after, and the affected link indices — enough to drive a signal-state
timeline and a "why did this change" overlay without re-running anything.

### What does NOT exist yet — the frontend's first task

**There is no per-step vehicle position data anywhere in the repository.** No
FCD, netstate or trajectory output is configured. Nothing above contains where
vehicles were at time *t*.

This is not a blocker for *starting*; it defines what to build first. Two routes:

* **Live streaming (the architecture's intent):** Stage 4, FastAPI + WebSocket,
  reading positions from the existing TraCI loop. The TraCI control path already
  exists in `ems_sim/counterfactual/runner.py`.
* **Recorded playback:** add `--fcd-output` to `SumoRunOptions` and replay the
  file. Simpler, deterministic, and reproduces a committed run exactly — likely
  the better first step.

**Whichever is chosen, the ambulance must keep being observed, never driven.** If
the frontend ever positions the ambulance itself, the thing on screen stops being
the experiment's result.

## 3. Constraints the visualisation must respect

1. **Label everything simulated.** Every view showing travel time, delay or
   saving must carry that it is a simulation under estimated inputs. The whole
   value of this project is that its numbers are honest about what they are.

2. **Do not visualise the traffic-side metric as a policy cost or benefit.** It
   is `DIAGNOSTIC_ONLY` (report §18), has no stable sign across seeds, and
   reproduces with no ambulance present. A red/green "network impact" gauge
   driven by it would be the single most misleading thing this project could
   ship. If shown at all, show it as a sensitivity diagnostic with its spread.

3. **Show the counterfactual as a pair.** The result is a *difference* between
   paired runs. A single-arm animation of an ambulance under preemption asserts
   nothing.

4. **Do not present ~12.5 s as the benefit of signal priority.** It is what this
   scenario yields when the ambulance meets a red. Section 16.5 shows a
   defensible alternative assumption where the same policies yield ~3 s because
   the ambulance met green.

5. **Render grade separation from `layer_effective`,** not by guessing from
   geometry. Silk Board has a double-decker flyover and an underpass; flattening
   them would put vehicles through each other.

6. **The `(u/c)` flyover `way/1351994264` has unresolved operational status.** It
   is included by default. If it is drawn, it should be visually marked as
   unresolved rather than shown as an ordinary road.

## 4. Status update — Phase 7 implemented

The gap identified in section 2 has been closed. `SumoRunOptions` now supports
`--fcd-output`, `scripts/export_scene.py` produces the scene payload, and an
interactive R3F viewer renders it. Full account:
[`3D_ARCHITECTURE.md`](../3D_ARCHITECTURE.md).

The milestone below — verify an on-screen position against its SUMO position by
direct comparison rather than by eye — **has been met and exceeded**:
`scripts/validate_scene_coords.py` reports **577 checks, 0 failures**, including
492 round-trip position checks at 0.0000 m maximum error and 0 continuity
violations across 132,778 vehicle samples.

The paired counterfactual view remains the next step: only `NORMAL` has been
exported, and the renderer's extension points for policy comparison are described
in `3D_ARCHITECTURE.md` section 12.

## 5. Recommended first milestone

Replay one committed run — `two_signal`, seed 42, NORMAL — from recorded
positions, and verify a vehicle's on-screen position against its SUMO position at
the same simulation step **by direct comparison, not by eye**. That is the Phase 7
"done when" condition already in [`ROADMAP.md`](ROADMAP.md), and it is the right
first thing to be true.

Then add the paired EMS_NEXT run for the same seed, so the first thing the scene
shows is a counterfactual pair rather than a single arm.
