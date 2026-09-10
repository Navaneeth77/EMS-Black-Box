# database/

PostgreSQL 16 + PostGIS 3.4, via `docker-compose.yml` (profile `database`).

**Nothing depends on this yet.** It stays off by default. Start it only when you
want it:

```bash
docker compose --profile database up -d
```

Note: the Compose plugin is not installed on this machine — see
`docs/ENVIRONMENT.md` for the workaround.

| Path | Purpose |
|---|---|
| `init/` | Runs once on first container init, in filename order. |
| `schema/` | Application schema. Empty: see below. |

## Why `schema/` is empty

The tables will hold road geometry, an intersection registry, run metadata
(config hash + seed) and per-run measurements. Their columns should follow from
what the ingestion pipeline and the TraCI loop actually produce.

Designing them now would mean guessing at the shape of data that does not exist —
and a speculative schema tends to get populated with values chosen to fit it.

Connection (local development only):

```
postgresql://ems:ems_local_dev@localhost:5433/ems_black_box
```
