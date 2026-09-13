# Central Silk Board: historical observed traffic (HISTORICAL_DEMO)

This folder holds the historical, published traffic values for Central Silk Board Junction, Bengaluru, that drive the HISTORICAL_DEMO replay. It also records how those values become SUMO demand.

HISTORICAL_DEMO is a presentation mode. It is not the frozen Phase 5 research experiment. It has its own network variant, trip, demand and output namespace. It reads no research output and writes none.

| File | What it holds | Labels |
|---|---|---|
| `observed_counts.json` / `.csv` | Only values printed in the cited reports | OBSERVED only |
| `provenance.json` | Every source investigated, used or not, with organisation, dates, location, units, URL | OBSERVED (per value) |
| `demand_conversion.json` | The OBSERVED → SUMO chain, written by `scripts/export_scene.py --historical` | OBSERVED, DERIVED, ESTIMATED, SIMULATED |
| `source_notes.md` | This file | — |

## Label vocabulary

- **OBSERVED**: a value printed in a cited survey report, copied without change.
- **DERIVED**: arithmetic on OBSERVED values from the *same* source, such as a ratio of two columns of one table. No assumption is added.
- **ESTIMATED**: needs an assumption made by this project, or combines values from different surveys.
- **SIMULATED**: produced by SUMO. This covers vehicle trajectories, signal states, queues and travel times.

## Selected sources

### 1. Junction volume: Comprehensive Mobility Plan for Bengaluru (Draft, October 2019), Table 2-13

- **Location:** Silk Board Junction, row 11 of 14 surveyed intersections, 4-legged.
- **Values:** four separate quantities. They are never interchanged.

  | Quantity | Value | Unit |
  |---|---:|---|
  | Peak-hour volume | 22,634 | vehicles per hour |
  | Peak-hour volume | 18,180 | PCU per hour |
  | 24-hour volume | 323,099 | vehicles per 24 hours |
  | 24-hour volume | 281,521 | PCU per 24 hours |

- **Page:** printed page 2-24 (PDF page 73).
- **Table source line:** the Revised Master Plan 2031 for Bangalore (Draft).
- **Organisation and authors:** not named in the text-extractable pages. The cover page is an image.
- **URL:** <https://data-opencity.sgp1.cdn.digitaloceanspaces.com/Documents/Recent/Bengaluru-Comprehensive-Mobility-Plan-October-2019-Draft.pdf>

The table publishes totals only. It gives no vehicle-class split and no per-movement turning counts for Silk Board, although the counts were described as classified turning volume counts.

### 2. When the count was taken: Revised Master Plan for Bengaluru 2031 (Draft), Volume-3, Bangalore Development Authority

Section 4.3 (printed page 17, PDF page 30) gives the traffic survey campaign:

- **Survey window:** December 2014 to April 2015.
- **Turning volume counts:** 14 locations, 24 hours for one day.

This matches the 14 intersections in CMP Table 2-13. The day on which Silk Board itself was counted is not reported, and neither is the clock time of its peak hour.

URL: <https://data-opencity.sgp1.cdn.digitaloceanspaces.com/Documents/Recent/Bengaluru-BDA-RMP-2031-Volume_3_MasterPlanDocument.pdf>

The separate Traffic & Transport sub-volume on bdabangalore.org could not be retrieved, because the TLS certificate did not match the redirected host. It may contain per-movement counts; they were not available to this project.

### 3. Car share and cycle length: Vani A, Madhu Singh and Prem Swaroup Reddy M, IJIRSET 6(6), June 2017

- **Title:** "Traffic Management Study for ORR, Bengaluru Jayadeva Intersection to Silk Board Intersection". DOI 10.15680/IJIRSET.2017.0606056.
- **Car share, p. 10540:** the Silk Board recommendations give cars as 53% of total vehicle volume.
  - Label: OBSERVED.
  - Scope caveat: the figure appears inside a recommendation about the left turn from the Agara arm. The paper does not say whether it is the whole-junction share. This project *interprets* it as the junction share.
- **Existing cycle length, p. 10540:** 450 s. Label: OBSERVED. The paper's 270 s cycle is a VISSIM proposal and is not used.
- **Survey date:** not reported.
- **Count tables:** embedded as images, so no volume from this paper is used.
- **URL:** <http://www.ijirset.com/upload/2017/june/56_Publication%20_2_.pdf>

## Investigated but not used

- **BBMP tunnel DPR (Rodic Consultants, March 2025), filed in NGT O.A. 185/2025.**
  - Its August 2024 counts include no count at Silk Board Junction. The nearest is a mid-block count on NH-44 near Madiwala footbridge: 232,494 vehicles per day.
  - Its seven turning-count intersections do not include Silk Board.
  - Its composition figure is an average over nine cordons, not location-specific.
  - A mid-block count on one arm is not a junction count, so it is not substituted.
- **B-SMILE elevated corridor DPR (February 2026).** An April 2025 14-hour count on the Indiranagar–Domlur road, which is a different location.
- **CTTP for Bengaluru (KUIDFC, June 2011).** Silk Board appears only in corridor descriptions.
- **Urban Mobility India, "Traffic Survey and Analysis – Central Silk Board Junction".** Not located by three searches. No value from it is used.
- **IJRESM 2019 paper, Wikipedia, Deccan Herald.** These were inaccessible (HTTP 403), gave no counts, or are secondary reporting.

Full records are in `provenance.json`.

## What is OBSERVED, DERIVED, ESTIMATED and SIMULATED

**OBSERVED**
- The four junction volumes above, in their published units and periods.
- The survey window, Dec 2014 – Apr 2015.
- The count duration: 24 h, one day.
- The 4-legged junction type.
- The 53% car share, with the scope caveat above.
- The 450 s existing cycle length.

**DERIVED** (computed by `simulation/ems_sim/historical/demand.py`, context only, never used to set demand)
- Peak-hour share of daily vehicles: 22,634 / 323,099.
- PCU per vehicle in the peak hour: 18,180 / 22,634.
  - This ratio cannot be inverted into a vehicle-class split, because the PCU factors the survey used are not reported.
- PCU per vehicle over 24 hours: 281,521 / 323,099.
- Mean hourly vehicles over 24 hours: 323,099 / 24.

**ESTIMATED**
- **Demand scale k, and the ambulance's departure.** SUMO receives 22,634 × k vehicles per hour, and the ambulance sets off at a time chosen with it.
  - Why a scale is needed: the modelled 1.6 km network cannot carry the observed hour.
  - How both are chosen: `scripts/historical_scenario_sweep.py` searches two grids fixed before any run — k in {0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40}, searched from the top down because a larger k is a larger share of what was counted, and the departure in {600, 700, 800, 900, 1000} s, searched from the earliest. It takes the first pair that is **valid** (no teleports, the ambulance arrives, no signal state outside the programs, insertion backlog at most 5%) **and** shows the ambulance **caught in the four-way's queue**: at least 12 vehicles between it and the stop line at its first halt there, and not halted on the line itself.
  - Every run in that search is a NORMAL run. No EMS policy is run and no travel time is compared, so the scenario cannot have been tuned to flatter priority. The chosen pair is in `demand_conversion.json` and `data/processed/historical_demo/demand_scale_sweep.json`.
- **Origin–destination spread.** No Silk Board turning movement count was found. The research model's committed OD pairs keep their relative weights.
- **Non-car vehicle split.** The 47% that is not cars is split in the research model's ESTIMATED proportions. No observed breakdown exists.
- **Applying the 2017 car share to the 2014–15 volume.** The two come from different surveys, so this is ESTIMATED. The implied 11,996 cars in the peak hour is labelled ESTIMATED for the same reason.
- **Signal timing.**
  - One controller for the four approach stop lines.
  - Equal green split.
  - 3 s yellow and 2 s all-red.
  - Clockwise phase order.
- **SUMO time-to-teleport of 600 s,** so a vehicle waiting through one full 450 s cycle is not removed.
- **The ambulance trip,** chosen by a rule fixed before any run. See `data/processed/historical_demo/trip_selection.json`.
- **Road elevation,** from OSM layer ordinals, with ramps at structure ends.

**SIMULATED**
- Every vehicle position, speed and heading.
- Every signal state shown.
- All queues, waiting times and travel times, including the NORMAL vs EMS comparison.

## Caveats a viewer should know

1. **Time mismatch.** The junction was counted in 2014–15. The road network is built from current OpenStreetMap and includes grade-separated structures, such as the Ragigudda–Silk Board integrated flyover, that were not open at the time. The replay therefore places a historical demand level on a present-day network.
2. **Flyover traffic.** It is not stated whether the junction count includes vehicles on the Silk Board flyover.
3. **Not traces.** SUMO trajectories are not historical GPS traces, and the signal states are not observed signal logs.
