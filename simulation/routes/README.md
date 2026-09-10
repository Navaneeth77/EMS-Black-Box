# simulation/routes/

`duarouter` output: one explicit route per vehicle over the real network.

Git-ignored — regenerable. Routing runs **without** `--ignore-errors` and
`--repair`: an OD pair that cannot be connected is a finding about the network,
and a repaired route is a different journey from the one the demand asked for.

Validated before anything is simulated — connectivity, grade transitions,
boundary entry and exit, and use of infrastructure Phase 2.5 left unresolved.
See `docs/BASELINE_SIMULATION.md` §6.
