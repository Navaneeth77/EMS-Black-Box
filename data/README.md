# data/

| Directory | Contents | In git? |
|---|---|---|
| `raw/` | Downloads exactly as retrieved. Never edited in place. | No (too large) |
| `processed/` | Derived artefacts: SUMO networks, routes, extracted tables. | No (regenerable) |
| `provenance/` | One JSON record per dataset. | **Yes** |

Provenance records are committed even though the files they describe are not.
That is deliberate: the record is the audit trail for a file too large to commit,
and it is what makes a missing or changed data file detectable.

Schema: `backend/app/models/provenance.py`. Policy: `docs/DATA_INTEGRITY.md`.
