# data/processed/

Artefacts derived from `data/raw/`: SUMO networks and routes, intersection
registries, extracted geometry.

Git-ignored because they are regenerable — but only if the transformation was
recorded. Every artefact needs a provenance record naming its inputs and the
command or config that produced it.

### Current contents

`silk_board_v1/` — road network derived from the OSM extract: `network.gpkg`
(layers `edges`, `nodes`, `junctions`, `intersections`), GeoJSON copies of the
junction, intersection and elevated-structure tables, `turn_restrictions.json`,
`study_area.json` and `validation_report.json`.

Regenerate with `python scripts/ingest_study_area.py`.
Provenance: `data/provenance/silk_board_v1_network_processed.json`.
