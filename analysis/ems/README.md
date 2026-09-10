# analysis/ems/

EMS-specific analysis outputs.

Phase 5's metric computation lives in
`simulation/ems_sim/counterfactual/metrics.py` — paired comparison, traffic-side
cost reported both including and excluding the ambulance, and per-intersection
attribution. Its results are written to
`data/processed/silk_board_v1/counterfactual/`.

This directory is for the aggregation that follows once seeds 43–46 have been
run: distributions of paired differences per policy, so a recovered time can be
stated with its uncertainty rather than as a single number.

**Not yet populated.** Seed 42 has been run and reported; the remaining seeds are
pending review of that result.
