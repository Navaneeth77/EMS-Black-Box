# simulation/results/

Per-run SUMO output: `tripinfo.xml`, `summary.xml`, `statistics.xml`,
`queues.xml`, `vehroutes.xml`, plus `measurements.json` with the TraCI samples
and the reproducibility block.

Git-ignored — regenerable from the committed configuration plus the recorded
seed. `calibration.json` and the `calib_*` directories are the demand-scale sweep
documented in `docs/TRAFFIC_DEMAND.md` §4.

All `SIMULATED_DATA`, produced from `ESTIMATED_DATA` demand. Not measurements of
Bengaluru traffic.
