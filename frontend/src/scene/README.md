# src/scene/

The Three.js / React Three Fiber scene. **Empty on purpose.**

The scene is Phase 6 (see `docs/archive/ROADMAP.md`). Building it now would mean
designing against imagined data: its geometry comes from the SUMO network, its
per-frame updates from the state schema the TraCI loop produces, and its
coordinate transform from the projection recorded in the `.net.xml`. None of
those exist yet.

When it is built, the constraint it must hold to:

> **Render state; do not produce it.**

Vehicle positions come from received frames. Concretely:

- **Permitted:** interpolating between two frames already received, for display
  smoothness only.
- **Not permitted:** extrapolating past the newest frame, continuing motion when
  frames stop arriving, spawning vehicles the simulation did not report, or
  feeding any display-side value back into a reported measurement.

The reason is that the picture is meant to be evidence. A viewer should be able
to point at the screen and say "that is what SUMO produced" — which stops being
true the moment the browser fills a gap on its own initiative.

Coordinate handling: SUMO is Z-up with Y north, Three.js is Y-up, so
`scene.x = sumo.x - originX`, `scene.z = -(sumo.y - originY)`, `scene.y = elevation`.
Omitting the Z negation mirrors the entire scene, which looks plausible until
someone compares it against a map. Re-anchoring to a junction-local origin is not
optional either: Three.js renders in float32, and raw UTM easting near Bengaluru
(~780,000 m) sits where float32 spacing is about 6 cm — enough to make vehicles
visibly jitter.

Details: `docs/ARCHITECTURE.md` §5 and `simulation/ems_sim/network/coordinates.py`.
