# data/provenance/

One JSON record per dataset, named `<dataset_id>.json`. Committed.

Schema: `backend/app/models/provenance.py` (`ProvenanceRecord`).
Policy and per-label required fields: `docs/DATA_INTEGRITY.md`.

### Current records

| File | Describes |
|---|---|
| `silk_board_v1_osm_raw.json` | The untouched OSM extract (`PUBLICLY_SOURCED_DATA`) |
| `silk_board_v1_network_processed.json` | The derived network tables, chained to the raw record |

The placeholder example has been removed now that real records exist: two records
that show the actual shape are a better template than an invented one, and there
is no longer any chance of the example being mistaken for data.

Verify a record still matches the file it describes:

```python
from ems_sim.provenance import read_record, verify_record
from pathlib import Path

ok, message = verify_record(
    read_record(Path("data/provenance/silk_board_v1_osm_raw.json")), Path(".")
)
```
