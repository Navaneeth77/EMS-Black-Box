# External traffic data

## HISTORICAL_DEMO: published Silk Board counts

[`historical_central_silk_board/`](historical_central_silk_board/) holds
historical, **published** traffic values for Central Silk Board Junction:

- the junction's peak-hour and 24-hour volumes, from the CMP 2019 draft
  Table 2-13, which draws on the RMP-2031 counts of Dec 2014 – Apr 2015;
- a 53% car share and a 450 s existing cycle, from IJIRSET 2017.

These values drive the **HISTORICAL_DEMO** presentation replay only. They never
feed the frozen research experiment. Every value carries an
OBSERVED / DERIVED / ESTIMATED / SIMULATED label, and the full chain from count
to SUMO demand is recorded in its `demand_conversion.json`. See
[`docs/HISTORICAL_DEMO.md`](../../docs/HISTORICAL_DEMO.md).

## Live / time-series feeds: none usable

**Nothing in `provenance/`, `raw/` or `processed/` feeds the simulation.** They
record an investigation into live or time-series feeds whose answer was no.

`provenance/traffic_data_investigation.json` documents every source checked for
real-time or historical traffic data covering Central Silk Board, and why none
was usable. The short version:

* **TomTom** — 401, licensed key required, and redistribution conditions this
  project cannot meet.
* **data.gov.in** — no reachable machine-readable traffic resource.
* **Bengaluru police / B-SMILE** — PDFs, not time series.
* **A GitHub "Bengaluru traffic" CSV** claiming 275 Silk Board rows — fetched,
  tested, and **rejected as synthetic**: its weekday/weekend volume ratio is
  0.984, meaning it has no weekly structure at all, and it reports 81.9 km/h
  average speed at India's most congested junction.

Using that CSV would have made the demo look better sourced than it is. It is
not used.

Demand therefore remains the project's own model, classed `ESTIMATED_DATA`, and
the UI says so. `raw/` and `processed/` are kept empty so the adapter has a home
if a licensed feed is ever added.
