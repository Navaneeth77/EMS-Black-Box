# simulation/config/

Generated `.sumocfg` scenario configurations.

Git-ignored — regenerable from the demand configuration. Paths inside are
relative to the config file's own directory, which is what SUMO resolves them
against.

Run one directly with the resolved binary (never the `sumo` on PATH, which is
the GUI launcher here):

```bash
"$SUMO_HOME/bin/sumo" -c simulation/config/<demand_id>.sumocfg
```
