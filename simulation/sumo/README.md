# simulation/sumo/

Generated SUMO scenarios: `.net.xml`, `.rou.xml`, `.sumocfg`.

Git-ignored, because they are rebuilt from committed configs plus a seed. What
gets committed is the recipe — the `.netccfg`, the scenario definition — not the
output.

Empty. No map has been ingested (Phase 1, `docs/archive/ROADMAP.md`).

The health endpoint reports `scenario: not_configured` until a `.sumocfg`
appears here.
