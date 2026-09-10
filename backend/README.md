# backend/

FastAPI + Pydantic. Transport layer only.

```bash
source ../.venv/bin/activate          # or: ./scripts/bootstrap.sh from repo root
uvicorn app.main:app --reload --app-dir backend --port 8000
```

- <http://localhost:8000/api/health>
- <http://localhost:8000/docs>

## Scope

This service holds no traffic model. It cannot compute a vehicle position, and
that is deliberate — a transport layer that *could* interpolate eventually would,
and then the numbers on screen would no longer be SUMO's.

Simulation lives in `simulation/`, analysis in `analysis/`. The backend forwards
what they produce.

## Layout

| Path | Contents |
|---|---|
| `app/main.py` | App factory, CORS, router wiring |
| `app/config.py` | Environment-backed settings (`EMS_` prefix) |
| `app/api/routes/health.py` | Health and per-dependency readiness |
| `app/api/routes/simulation.py` | Planned run/replay endpoints — no routes yet |
| `app/models/provenance.py` | The four data-class labels and the record schema |
| `app/models/schemas.py` | API response models |
| `app/ws/stream.py` | State streaming — contract documented, not implemented |

## Health semantics

`status` and `simulation_ready` answer different questions on purpose: the API
can be perfectly healthy while being unable to simulate anything. Conflating them
would let the service overstate what it knows, which is the failure mode this
project is built to avoid. `simulation_ready` stays False until a SUMO
installation *and* a built scenario are both present.
