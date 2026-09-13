# Licensing — unresolved, and why this file is not a licence

**This repository has no licence.** There is no `LICENSE` file, no `license`
field in `pyproject.toml` or `frontend/package.json`, and no copyright notice in
any source file.

That is a decision, whether or not it was made deliberately: with no licence,
default copyright applies and nobody other than the copyright holder may copy,
modify or redistribute this work. If the intention is for anyone to be able to
read, run or build on it, a licence has to be added.

This file exists because the audit could not add one responsibly. Choosing a
licence requires facts this repository does not contain, and an audit that
guessed at them would be producing a legal conclusion out of nothing.

## What has to be established first

1. **Who owns the copyright.** Nothing in the repository names an institution,
   a supervisor, a funder or an employer. If this work was produced as part of a
   degree, an internship or employment, the owner may not be the author, and the
   governing IP policy decides what may be published and under what terms.
2. **Whether any obligation attaches to publication.** Academic work is
   sometimes subject to an embargo, a thesis-submission condition, or a rule
   about publishing before examination.
3. **What the intended use is.** Permissive (MIT, BSD-3, Apache-2.0 — the last
   adds an explicit patent grant), weak copyleft (MPL-2.0), or strong copyleft
   (GPL-3.0) are different answers to "what may someone do with this", not
   interchangeable defaults.

## The data complicates the code answer

A single licence line at the root would be wrong here, because the repository
contains three kinds of thing with different origins:

| What | Origin | Constraint |
|---|---|---|
| Source code (`simulation/`, `backend/`, `frontend/`, `scripts/`, `tests/`) | Written for this project | Whatever the copyright holder chooses |
| **Derived geodata** (`data/processed/**.geojson`, the SUMO networks, `network.gpkg`) | Computed from an OpenStreetMap extract | OSM is **ODbL 1.0**. A derived database carries ODbL's share-alike and attribution obligations. |
| **Observed traffic counts** (`data/traffic/historical_central_silk_board/`) | Transcribed figures from published third-party reports (CMP 2019, RMP-2031, IJIRSET 2017), each cited with a source URL | Facts are not copyrightable, but the reports are. This repository quotes individual figures and links the sources; it does not redistribute the documents, and should not start. |

The OSM extract itself is **not** committed — `data/raw/` holds only a README and
a `.gitkeep`, and `scripts/ingest_study_area.py` downloads it — but the processed
geodata derived from it is committed, and `data/provenance/silk_board_v1_osm_raw.json`
already records the required attribution:

> © OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)

Any licence added to this repository needs to either keep that attribution with
those files or state that the derived geodata is under ODbL while the code is
under something else. Both are ordinary, and both need to be written down.

## Suggested shape, if and when the ownership question is answered

Not a recommendation of which licence — that is the owner's decision — but the
structure that fits what is here:

- `LICENSE` at the root for the code, whichever licence is chosen;
- a short `DATA_LICENSE` or a section in this file stating that the derived
  geodata is ODbL 1.0 with the attribution above, and that the transcribed
  traffic figures are cited from their sources rather than relicensed;
- the same licence identifier added to `pyproject.toml` (`license = "…"`) and
  `frontend/package.json` (`"license": "…"`), so the metadata agrees with the
  file.

Until then this repository should be treated as **all rights reserved**, and the
audit has left it that way rather than choosing for its author.
