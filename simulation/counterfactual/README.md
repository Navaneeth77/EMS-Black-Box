# simulation/counterfactual/

Artifacts for counterfactual runs. The code lives in
`simulation/ems_sim/counterfactual/`:

| Module | Contents |
|---|---|
| `pairing.py` | Scenario identity — computed, compared, and **refused** on mismatch |
| `runner.py` | The policy-aware TraCI loop, signal validation, teleport detection |
| `metrics.py` | Paired differences against NORMAL of the same seed, and attribution |

Per-run outputs go to `simulation/results/cf_<area>_seed<N>_<POLICY>/` and are
git-ignored; the small JSON records under
`data/processed/<area>/counterfactual/` are committed.

Differences are only ever taken against the NORMAL run of the **same seed**.
Nothing is called time saved until it has been paired that way — Phase 4 measured
the ambulance seed-to-seed spread at 1.14 s, and comparing across seeds would
fold that into the policy effect.
