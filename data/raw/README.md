# data/raw/

Downloads exactly as retrieved — OSM extracts, any published datasets obtained
later. **Never edited in place.** Derive into `data/processed/` instead, so the
original stays checksummable against its source.

Every file needs a record in `data/provenance/` with source, URL, licence,
retrieval timestamp and SHA-256.

### Current contents

`silk_board_v1/silk_board_v1.osm.xml` — OpenStreetMap extract for the Central
Silk Board study area, retrieved 2026-09-09 from the Overpass API.
Provenance: `data/provenance/silk_board_v1_osm_raw.json`.
Details: `docs/STUDY_AREA.md`.

The file is git-ignored (4.8 MB); its provenance record is committed, and the
recorded SHA-256 is what makes an edit-in-place detectable.
