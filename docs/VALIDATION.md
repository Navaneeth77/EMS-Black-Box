# Validation — final state

What has been run, what passed, and what was deliberately not run. The numbers
themselves are in [`RESULTS.md`](RESULTS.md); the design is in
[`METHOD.md`](METHOD.md); what none of it supports is in
[`LIMITATIONS.md`](LIMITATIONS.md).

> **Simulated results only.** Not a measurement of real ambulance performance or
> of real traffic in Bengaluru.

The pre-audit validation gate is kept verbatim at
[`archive/FINAL_VALIDATION.md`](archive/FINAL_VALIDATION.md). Where it disagrees
with this file, this file is current and the archive is what was believed then.

---

## 1. Experiments completed

| Experiment | Namespace | Runs | Disturbance |
|---|---|---:|---|
| Free-flow baseline (pre-audit) | `two_signal_*` | 20 | none |
| Free-flow control, corrected code | `NO_INCIDENT_CONTROL_*` | 20 | none |
| Incident matrix (pre-audit) | `inc_two_signal_*` | 20 | committed obstruction |
| Incident matrix, corrected code | `corrected_inc_two_signal_*` | 25 | committed obstruction |
| Congestion severity sweep | `CONGESTION_{LOW,MEDIUM,HIGH}_inc_*` | 60 | the same obstruction at ×4, ×1, ÷4 discharge |
| Ambulance-free controls | `control/` (Phase 5a, 5b) | — | — |
| HISTORICAL_DEMO replay | `historical_demo/` | 2 | demo obstruction set |

**105 counterfactual runs are committed as per-run records.** Nothing under
`data/processed/` was overwritten: every new experiment writes under its own
label beside the frozen results.

### Experiments intentionally not run

- **The 450 s historically sourced signal cycle.** Verified against the source
  PDF and **not run**. The paper gives a total cycle length and no phase splits,
  sequence, amber or all-red, and the junction it describes is not on the
  research ambulance's route (its two signals are 510 m and 1,011 m away, on
  different roads). Running it would have required inventing every phase split
  and transplanting one junction's cycle onto two others.
  [`METHOD.md`](METHOD.md) §3 states this in full.
- **Re-running the frozen matrices under the stop-line fix.** The fix was
  demonstrated not to change any metric (§4 below), so re-running 45 runs would
  have produced identical files under new names.

## 2. Automated gates

| Gate | Command | Result |
|---|---|---|
| Python tests | `pytest -q` | **583 passed** |
| Python tests, no simulator | `pytest -m "not sumo" -q` | **557 passed, 26 deselected** |
| Lint | `ruff check .` | **clean** |
| Frontend typecheck | `npm run typecheck` (`tsc -b`) | **clean** |
| Frontend tests | `npx vitest run` | **57 passed** (4 files) |
| Frontend build | `npm run build` | **ok** |
| HISTORICAL_DEMO | `scripts/validate_historical_demo.py` | **33/33** |
| Scene coordinates | `scripts/validate_scene_coords.py` | **PASS, 0 failures** over 349,527 vehicle samples |

`ruff format` is **not** a gate. The repository is not ruff-formatted and running
the formatter would rewrite a large fraction of the tree for no behavioural
reason; the lint rules that are enforced are in `pyproject.toml`.

## 3. Experiment integrity

Checked across the 100 runs of the four corrected matrices
(`NO_INCIDENT_CONTROL` + `CONGESTION_LOW/MEDIUM/HIGH` + `corrected_inc`):

| Property | Result |
|---|---|
| One `scenario_hash` per seed per experiment | yes |
| Four distinct `policy_hash` values within each | yes |
| Ambulance route identical in **every** run of **every** experiment | yes |
| One network SHA-256 across every experiment | yes |
| Driven route equals planned route | yes, every run |
| Signal conflicts | **0** |
| SUMO collisions | **0** |
| Ambulance teleports | **0** |
| Runs valid for headline use | all |
| Vehicle cohort identical between the incident and no-incident scenarios | yes — 3,226 identical vehicle IDs per seed |

Each run checks 15,598 applied signal states and every transition between them
against the junction's own foe matrix. Twenty runs is 311,960 states; the 25
corrected incident runs are **389,950**.

**Teleports are reported, never absorbed.** Background teleports rise with
severity — 14 (no incident), 12 (`low`), 22 (`medium`), 35 (`high`) per 20 runs
— and no teleported vehicle's travel time enters any travel-time metric. A run
whose *ambulance* teleported would be invalid for headline use; none did.

**Ambulance/building intersections and ambulance/vehicle overlaps** are geometric
properties of the exported 3D scene, not of a research run: the research network
carries no buildings and the counterfactual runs export no scene. They are
checked on the HISTORICAL_DEMO replay, where both are **0** across 320 and 593
frames against 2,484 buildings at 5 cm tolerance, and the research runs report
SUMO's own collision count, which is **0**.

## 4. Defects found and fixed in this pass

**The stop-line map was keyed by traffic light rather than by encounter.** The
route meets one signal twice, on approaches 19.55 m and 33.69 m long; the second
overwrote the first, so the first was measured to a position past the end of its
own edge. TraCI answered `Position on lane invalid`, the caller suppressed it,
and that encounter dropped out of the halt record.

- **Scope:** the halt detector's diagnostic annotation only. No travel time,
  waiting time, stop count, attribution or cohort figure depends on it.
- **Verification:** seed 42 was re-run under the fix in both scenarios across
  four policies. Every ambulance metric, vehicle count, teleport count and
  scenario hash came back **identical**, so no frozen result needed re-running.
- **Regression test:** `tests/simulation/test_stop_line_positions.py`. Four of
  its five tests fail if the `tls_id` keying is reintroduced.

**`validate_scene_coords.py --out` crashed** on a path outside the repository.
One line; the report itself was already correct.

## 5. Known gaps

- **`data/processed/silk_board_v1/scene_validation.json` is a pre-audit
  artefact** (generated 2026-09-11). Re-running the validator today produces one
  additional check — `ambulance_is_the_recorded_vehicle`, added by the audit —
  and the same verdict, PASS with 0 failures. The committed file was left as it
  is rather than silently rewritten, because check C22 of the demo validation
  exists precisely to catch a result file changing underneath the record. Anyone
  regenerating it should expect C22 to flag it and should say why.
- **CI has never executed.** The workflow at `.github/workflows/ci.yml` is
  committed, but the repository has no git remote, so no run exists. Every step
  it defines was run locally and is in §2.
- **No licence.** See [`LICENSING.md`](LICENSING.md). The repository is
  effectively all rights reserved, the derived geodata carries ODbL obligations,
  and the ownership question has not been answered, so none was added.

## 6. Provenance

Every run writes a record under `data/provenance/` carrying its `dataset_id`,
`data_class`, SUMO version, network SHA-256, scenario hash, component hashes
(network, demand, route, ambulance, disturbance, window), policy hashes, seed,
timestamp, derived-from list, processing steps and limitations.

The four labels are not mixed anywhere: `OBSERVED` (a figure printed in a cited
report), `DERIVED` (arithmetic on observed values from one source), `ASSUMED` /
`ESTIMATED` (a choice this project made), `SIMULATED` (produced by SUMO). The
congestion severities are `ASSUMED_SCENARIO_PARAMETER` and say so inside every
run record that used them.

**No simulated traffic is labelled observed anywhere in this repository.**
