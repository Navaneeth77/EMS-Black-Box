# simulation/

SUMO scenario construction, the TraCI control loop, signal policies, and the
ambulance trip. **This is the only place vehicle motion is produced.**

## Layout

| Path | Contents |
|---|---|
| `ems_sim/config.py` | `ScenarioConfig` — the reproducibility contract |
| `ems_sim/provenance.py` | The four data-class labels and record I/O **(implemented)** |
| `ems_sim/network/study_area.py` | Committed bounding box + OSM-derived anchor **(implemented)** |
| `ems_sim/network/osm_download.py` | Reproducible OSM acquisition **(implemented)** |
| `ems_sim/network/osm_parse.py` | OSM XML → attribute-preserving tables **(implemented)** |
| `ems_sim/network/validation.py` | 33 checks on an extract **(implemented)** |
| `ems_sim/network/pipeline.py` | acquire → validate → write + provenance **(implemented)** |
| `ems_sim/network/sumo_convert.py` | netconvert configuration + runner **(implemented)** |
| `ems_sim/network/sumo_validate.py` | Structural validation of the .net.xml **(implemented)** |
| `ems_sim/network/sumo_review.py` | Phase 2.5 review: operational status, TLS, ramps, merges, defaults **(implemented)** |
| `ems_sim/network/sumo_pipeline.py` | convert → validate → review → provenance **(implemented)** |
| `ems_sim/demand/vehicle_types.py` | Seven vTypes, every parameter with a basis **(implemented)** |
| `ems_sim/demand/config.py` | Seeded boundary-to-boundary demand **(implemented)** |
| `ems_sim/demand/ambulance.py` | The ambulance trip; no priority yet **(implemented)** |
| `ems_sim/demand/generator.py` | flows → duarouter → routes **(implemented)** |
| `ems_sim/demand/route_validation.py` | Route checks before simulating **(implemented)** |
| `ems_sim/demand/baseline.py` | The Phase 3 pipeline **(implemented)** |
| `ems_sim/calibration/scenario.py` | Unresolved-infrastructure scenario flag **(implemented)** |
| `ems_sim/calibration/multi_seed.py` | Multi-seed runs + edge/junction measurements **(implemented)** |
| `ems_sim/calibration/aggregate.py` | Cross-seed statistics, simulation bottlenecks **(implemented)** |
| `ems_sim/calibration/determinism.py` | Same-seed reproducibility **(implemented)** |
| `ems_sim/calibration/sensitivity.py` | Mix and behaviour experiments **(implemented)** |
| `ems_sim/calibration/audits.py` | Signal-program and speed audits **(implemented)** |
| `ems_sim/runner/sumo_process.py` | SUMO launch options **(implemented)** |
| `ems_sim/runner/traci_bridge.py` | The step loop + teleport detection **(implemented)** |
| `ems_sim/runner/measurements.py` | What is recorded, and what is left null **(implemented)** |
| `ems_sim/network/osm_ingest.py` | Module map for Phase 1 |
| `ems_sim/network/coordinates.py` | WGS84 ↔ SUMO ↔ scene frames *(stub)* |
| `ems_sim/runner/sumo_env.py` | SUMO installation discovery **(implemented)** |
| `ems_sim/runner/sumo_process.py` | Launching SUMO *(stub)* |
| `ems_sim/runner/traci_bridge.py` | The step loop *(stub)* |
| `ems_sim/policies/` | Fixed-time baseline, EMS preemption *(stubs)* |
| `ems_sim/state/` | State frame schema — empty, follows from the loop |
| `demand/`, `routes/`, `config/`, `results/` | Generated demand, routes, scenario configs, run outputs |
| `sumo/<area>/*.netccfg` | Committed conversion recipe — an input, not an output |
| `sumo/<area>/*.net.xml` | Generated network (git-ignored; rebuilt from the netccfg) |
| `scenarios/` | Committed scenario definitions |

## Why the stubs raise instead of returning defaults

Every one of these functions returns a number that ends up in a result. A stub
that returned a plausible default would produce a system that runs end-to-end and
reports fabricated figures — much worse than one that refuses to run. Each stub's
docstring records the intended method and the decisions that need making.

## Phase 1 ingestion

```bash
python scripts/ingest_study_area.py     # download, validate, write, record provenance
python scripts/plot_study_area.py       # render for human review
```

Result and limitations: `docs/STUDY_AREA.md`.

Two properties the pipeline enforces rather than hopes for:

- **It never fabricates.** If no OSM source responds, it raises and writes
  nothing. There is no cached sample to fall back on.
- **It refuses to write a network that would simulate wrongly.** An extract that
  fails an ERROR check — the flyover flattened, the bbox mismatched, the extract
  runaway — stops the pipeline instead of producing output that converts cleanly
  and reports wrong numbers.

## Phase 2 conversion

```bash
python scripts/build_sumo_network.py    # convert, validate, record provenance
python scripts/plot_sumo_network.py     # render for human review
```

Result and limitations: `docs/SUMO_CONVERSION.md`.

The same two properties hold as in Phase 1: it never fabricates, and it refuses
to hand over a network that fails an ERROR check — including the one that would
matter most, a flyover flattened into the junction.

## traci and sumolib

Not pip dependencies. They ship inside `SUMO_HOME/tools` and must match the
installed SUMO exactly; a PyPI copy at a different version surfaces as opaque
TraCI protocol errors. `ems_sim.runner.sumo_env` resolves the path at runtime.

## Reproducibility

`ScenarioConfig.config_hash()` deliberately excludes the policy, so a baseline and
its counterfactual hash identically. That equality *is* the statement "these are
the same scenario" — and the analysis refuses to compare runs whose hashes or
seeds differ.
