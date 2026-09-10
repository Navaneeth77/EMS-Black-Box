# simulation/demand/

Generated flow definitions (`*.flows.xml`): `<vType>` blocks, background flows
between boundary terminals, and the ambulance trip.

Git-ignored — regenerable from the committed demand configuration in
`ems_sim.demand.config` plus the recorded seed. What gets committed is the
recipe, not the output.

**Do not edit these by hand.** An edited demand file has no provenance, and its
results cannot be defended.

Every file carries its `ESTIMATED_DATA` / `SIMULATED_DATA` labels in its header,
so a file separated from its report still says what it is.

Regenerate: `python scripts/run_baseline.py --demand-only`
