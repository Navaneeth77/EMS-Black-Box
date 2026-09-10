# Data integrity

This project makes claims about a real junction in a real city. Those claims are
only usable if a reader can tell, at a glance, which numbers were measured, which
were sourced, and which the project assumed.

## The four labels

Every dataset, parameter and reported figure carries exactly one.

### `VERIFIED_REAL_DATA`
From a primary source **and** independently checked against that source.

Requires: source, retrieval date, and a record of how it was verified.
Currently in use: **nothing**. No dataset has met this bar yet.

### `PUBLICLY_SOURCED_DATA`
From a public, citable source; real, but not independently verified by us.

Requires: source name, URL, licence, retrieval timestamp, checksum.
Expected first use: the OSM extract for the study area. It is real road geometry
from a citable source, and it is community-maintained rather than surveyed by us
— which is exactly what this label says.

### `ESTIMATED_DATA`
An assumption made by this project because real data was unavailable.

Requires: the reasoning, and whatever it was derived from.
Expected uses: signal timings, traffic demand and composition, manual corrections
to OSM geometry.

**Never presented as real, in any medium — docs, API responses, UI, papers, demo
narration, commit messages.**

### `SIMULATED_DATA`
Produced by SUMO or by analysis in this repository.

Requires: the config hash and the random seed that generated it.
Uses: all travel times, delays, recovered times and rankings.

---

## Why the estimated/real line is the one that matters

The most consequential estimate in this project is the **baseline signal
timings**. Bengaluru signal plans are not published openly, so any cycle length,
split or offset used here is our assumption.

That estimate is the denominator of every result. "EMS priority recovered 47
seconds" means "47 seconds relative to *this assumed* fixed-time plan". A reader
who believes the baseline is measured will take the result as far stronger than
it is.

Note carefully what this does and does not undermine:

- **The comparison stays valid.** Both arms use the same assumed baseline, so the
  difference between them isolates the policy.
- **The interpretation depends entirely on the label.** The result describes a
  modelled junction, not a measured one. Drop the label and the same number
  becomes a claim about the real Silk Board — a claim this project has not
  earned.

Should documented timings become available, the estimate is replaced, the label
changes, and the runs are repeated from their saved configs. That path is only
open if the estimate was labelled honestly in the first place.

---

## Provenance records

Everything in `data/` has a record in `data/provenance/`, named
`<dataset_id>.json`. The schema is `backend/app/models/provenance.py`; a filled
example is `data/provenance/EXAMPLE_osm_extract.json`.

Records are committed even though the data files they describe are usually
git-ignored. That is the point: the record is the audit trail describing a file
too large to commit.

Required fields by label:

| Label | Also required |
|---|---|
| `VERIFIED_REAL_DATA` | source, URL, licence, retrieval timestamp, checksum, verification method |
| `PUBLICLY_SOURCED_DATA` | source, URL, licence, retrieval timestamp, checksum |
| `ESTIMATED_DATA` | `estimation_basis` — the reasoning and its derivation |
| `SIMULATED_DATA` | `produced_by` (run ID / config hash) and `random_seed` |

---

## Reproducibility

A result without its seed is not a result.

Every run writes, alongside its output:

- the full `ScenarioConfig` as JSON,
- the `config_hash`,
- the `random_seed`,
- the SUMO version,
- the git commit of this repository.

Two runs may only be compared when their `config_hash` and `random_seed` match
and their policies differ. `analysis/ems_analysis/replay_diff.py` enforces this
and raises rather than returning a number when it fails — because a difference
between two subtly different scenarios is indistinguishable from a real result
once it reaches a chart.

---

## Rules of thumb

1. If you cannot say where a number came from, do not report it.
2. If a real value was unavailable and you assumed one, label it and say why.
3. Never fill a gap in real data with a plausible-looking value. A stated
   limitation is worth more than a fabricated completeness.
4. Raw downloads are never edited in place. Derive into `data/processed/` and
   record the transformation.
5. Keep measured and assumed visually distinct in every output. A reader should
   never have to consult the source to tell them apart.
