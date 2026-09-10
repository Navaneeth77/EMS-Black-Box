# Phase 4: baseline calibration

What the simulation-based baseline rests on, how sensitive it is to each
assumption, and what was deliberately left alone.

> **Nothing here makes the simulation empirically calibrated.** No observation of
> Silk Board traffic has been used at any stage of this project. Phase 4
> quantifies how much the result moves when each estimated assumption changes; it
> does not establish that any of them is right.

Companion: [`BASELINE_UNCERTAINTY.md`](BASELINE_UNCERTAINTY.md) (seed spread).
Machine-readable: [`calibration/`](../data/processed/silk_board_v1/calibration/)

```bash
python scripts/run_multi_seed_baseline.py     # 5 seeds
python scripts/verify_determinism.py          # same seed twice
python scripts/run_sensitivity.py --experiment both
```

---

## 1. Why no parameter was tuned

There is nothing to tune towards. Tuning means adjusting parameters until output
matches a measurement, and this project has no measurement of Silk Board. In its
absence "better" could only mean "closer to what I expected", which produces a
model that agrees with its author rather than with the world — and it would then
be quoted as though it agreed with Bengaluru.

So the experiments below change one thing at a time and report the size of the
effect. **The baseline is unchanged.** What the results support is a different
and more useful claim: which assumption a future field measurement should target
first.

---

## 2. Vehicle mix sensitivity

Four mix hypotheses, single seed (42), 1800 s. All `ESTIMATED_DATA`; none is a
measured or published Bengaluru modal split.

| Variant | motorcycle | car | Arrived | Mean time loss | Δ vs baseline | Ambulance | Δ |
|---|---|---|---|---|---|---|---|
| **baseline** | 50% | 26% | 1,515 | **62.64 s** | — | **146.0 s** | — |
| two_wheeler_heavy | 70% | 15% | 1,562 | 44.78 s | **−17.86 s (−28%)** | 144.0 s | −2.0 s |
| car_heavy | 25% | 50% | 1,485 | 60.32 s | −2.32 s (−4%) | 147.0 s | +1.0 s |
| heavy_vehicle_heavy | 40% | 22% | 1,469 | 76.67 s | **+14.03 s (+22%)** | 143.0 s | −3.0 s |

### Reading it

**Vehicle mix is the most sensitive assumption tested.** Mean time loss swings
**32.6 s** between the extremes — nearly three times the seed-to-seed standard
deviation of 12.0 s, so the effect is well clear of noise. More two-wheelers
means more throughput (1,562 arrivals vs 1,469) and less delay; more buses and
trucks means the opposite. That is the expected direction, and its *size* is the
finding.

**The ambulance is far less sensitive.** Its travel time moves within a 4 s band
(143.0–147.0 s) against a seed-to-seed range of 3.0 s. The mix effect on the
ambulance is barely distinguishable from noise, because its route runs on
high-capacity trunk corridors where composition matters less than on the minor
roads carrying most of the delay.

**Consequence:** a Phase 5 recovered time is unlikely to be sensitive to the mix,
but any statement about *network-wide* delay is very sensitive to it. The 50/26
baseline split is unsourced, and this is the assumption most worth replacing with
a real count.

---

## 3. Two-wheeler behaviour sensitivity

Five variants of the motorcycle vType, single seed (42), 1800 s.

| Variant | Change | Arrived | Mean time loss | Δ | Ambulance | Δ |
|---|---|---|---|---|---|---|
| **lc_baseline** | `lcAssertive=2.0` | 1,515 | **62.64 s** | — | **146.0 s** | — |
| lc_sumo_default | `lcAssertive=1.0` | 1,492 | 67.58 s | **+4.94 s (+8%)** | 146.0 s | 0.0 |
| lc_very_assertive | `lcAssertive=4.0` | 1,501 | 60.23 s | −2.41 s (−4%) | 145.5 s | −0.5 s |
| **lc_no_sublane_freedom** | `latAlignment=center` | 1,515 | **62.64 s** | **0.00 s** | 146.0 s | 0.0 |
| lc_larger_gap | `minGap=2.0, tau=1.0` | 1,479 | 65.68 s | +3.04 s (+5%) | 146.5 s | +0.5 s |

### Finding: half the lane-filtering model is inert

**`latAlignment` produced a result identical to the baseline — not approximately,
exactly.** The two runs' `tripinfo` outputs differ in 8 lines out of 1,565, and
all 8 are header metadata (a timestamp and file paths). Every one of the 1,557
trip records is identical.

The cause: SUMO's sublane model requires `--lateral-resolution`, and the baseline
does not set it. SUMO then defaults to −1, meaning *all vehicles drive at the
centre of their lane*, and `latAlignment` is ignored entirely.

So the two-wheeler model's **lateral freedom half does nothing**. Only the
gap-acceptance half (`lcAssertive`) is operating. The Phase 3 documentation
described `latAlignment=arbitrary` as encoding lane-filtering; that description
was optimistic, and this experiment is what caught it.

**Not changed.** Enabling the sublane model would alter the behaviour of every
vehicle in the network and invalidate the calibrated demand scale, the seed
spread and the Phase 3 baseline together. It is a Phase 5+ decision with a
recalibration attached, not a switch to flip here. It is recorded as a
limitation.

### Finding: `lcAssertive` matters, modestly

Reverting to SUMO's default of 1.0 costs **+4.94 s of mean time loss (+8%)** —
above the seed-to-seed noise of ~12 s? **No: below it.** The seed standard
deviation for background time loss is 12.0 s, so a 4.94 s effect from a
single-seed comparison is *not* separable from noise on this evidence.

That is the honest reading, and it is worth stating plainly: **the two-wheeler
lane-changing parameter is less consequential than Phase 3 feared.** The
uncalibrated `lcAssertive=2.0` is not what drives the observed queue behaviour.

**The ambulance is insensitive to all of it** — 145.5 to 146.5 s across every
variant, inside the 3.0 s seed range.

### Verdict

The baseline is preserved. `lcAssertive=2.0` stays because no defensible reason
to change it emerged: its effect is small, and SUMO's default is not more
"correct" for Indian traffic than the current value — it is merely the value
calibrated on European traffic.

---

## 4. Signal program audit

**8 traffic lights, all `static`, all 90 s cycles.**
`ESTIMATED_DATA / NETCONVERT_GENERATED`.

| TLS | Phases | Cycle | Green | Yellow | All-red | Signal groups | Green share per group (min/mean/max) |
|---|---|---|---|---|---|---|---|
| `joinedS_12074449284_…` | 6 | 90 s | 72 s | 18 s | 0 s | 24 | 0.27 / 0.52 / 1.00 |
| `GS_cluster_10282769895_…` | 4 | 90 s | 78 s | 12 s | 0 s | 13 | 0.43 / 0.48 / 1.00 |
| `306594280` | 3 | 90 s | 81 s | 4 s | 5 s | 3 | 0.90 / 0.90 / 0.90 |
| `494271080` | 3 | 90 s | 81 s | 4 s | 5 s | 3 | 0.90 / 0.90 / 0.90 |
| `3805003789` | 3 | 90 s | 80 s | 5 s | 5 s | 2 | 0.89 / 0.89 / 0.89 |
| `2971089260` | 3 | 90 s | 80 s | 5 s | 5 s | 1 | 0.89 |
| `11348815671` | 3 | 90 s | 82 s | 3 s | 5 s | 1 | 0.91 |
| `12611782078` | 3 | 90 s | 82 s | 3 s | 5 s | 1 | 0.91 |

### Only two of the eight actually control conflicting traffic

The green **share per signal group** is the figure that matters — how much of the
cycle a given movement gets. (Cycle-wide green summed across phases is near 0.9
for any sane program and says nothing.)

The two joined clusters show shares of **0.27–1.00**, which is what a real
signalised junction looks like: competing movements, each getting part of the
cycle. The other six give **0.89–0.91 to one or two groups** — they barely
restrict anything, and are the single-link artefacts Phase 2.5 flagged.

**Consequence for Phase 5:** an EMS priority policy has **two** meaningful
signals to act on, not eight. The other six have almost no red time to preempt.

**None of these is a Bengaluru signal timing.** Signal *locations* come from OSM;
*timings* do not exist in OSM and are not published for Bengaluru. Every phase
duration here was generated by netconvert from its defaults.

---

## 5. Speed provenance

| | Edges | Data class |
|---|---|---|
| **OSM-derived speed** | 140 | `PUBLICLY_SOURCED_DATA` |
| **SUMO-estimated speed** | 1,375 | `ESTIMATED_DATA` |

Per-edge listing with class, value and provenance in
`calibration/speed_provenance.json`. The word *real* is never used to describe a
SUMO-supplied value; a test asserts it.

### The 166 edges at ≥80 km/h

| Edge class | Count |
|---|---|
| highway.tertiary | 90 |
| highway.secondary | 33 |
| highway.trunk_link | 27 |
| highway.primary | 14 |
| highway.primary_link | 2 |

**12.67 km of road** given a free-flow speed of 80–100 km/h by netconvert's type
map, which is derived from European conventions. 90 of them are *tertiary* roads.

**Not replaced.** No source in this project supports a better value, and a guess
would be worse than a labelled default — it would look sourced and its provenance
would be lost.

**Why it matters:** time loss is measured against free-flow speed, so reported
time loss is an over-estimate on these edges.

---

## 6. Simulation bottlenecks

**Not real-world bottlenecks.** These are locations where *this simulation*
accumulated delay, on estimated demand with netconvert-generated signals.

Present in all 5 seeds:

| Rank | Junction | Type | Mean time loss | Roads |
|---|---|---|---|---|
| 1 | `1417443722` | priority | 89,149 s | (unnamed, tertiary + residential) |
| 2 | `494271078` | priority | 66,259 s | (unnamed tertiary) |
| 3 | `1417443736` | priority | 46,221 s | 1st/2nd Cross Road |
| 4 | `6196977039` | priority | 44,520 s | (unnamed) |
| 5 | `cluster_10282769895_…` | **traffic light** | 12,127 s | **Hosur Road, Madiwala Sarjapura Road** |

Most-delayed edges: `744561216#0` (tertiary, 2 lanes, 40 km/h) 84,030 s;
`370328588#2` (tertiary, 2 lanes, **80 km/h — SUMO-estimated**) 66,259 s;
`544999264#1` (residential, 1 lane) 46,221 s.

### The finding, and its limits

**The worst simulated congestion is not at Silk Board.** It is on a cluster of
minor roads around (12.913, 77.625), south-east of the junction — a rat-run that
duarouter routes through-traffic down. The Silk Board traffic light cluster ranks
fifth, an order of magnitude below.

**A hypothesis, not a conclusion:** inflated default speeds may be what makes
those minor roads attractive to the router. `370328588#2` is a *tertiary* road
the model believes runs at 80 km/h. But the evidence is mixed — 8 of the 15
worst edges have SUMO-estimated speeds and 7, including the very worst, are
OSM-derived. **Testing this properly needs a speed-corrected variant, which needs
sourced speeds, which this project does not have.** It is the top candidate for
investigation once field data exists.

---

## 7. Unresolved infrastructure

`way/1351994264` is **included by default** and never silently removed. Both
readings are runnable:

```bash
python scripts/run_multi_seed_baseline.py --variant include_unresolved   # default
python scripts/run_multi_seed_baseline.py --variant exclude_unresolved
```

Excluding it builds a separate network variant, because removing the edge also
disconnects the 1.9 km carriageway it is the sole connection to (Phase 2.5 §1) —
so the exclusion is a different network, not a routing filter.

**192 background vehicles per run use it; the ambulance does not, in any seed.**
The headline ambulance travel time is therefore not conditional on the unverified
fact, though the traffic it drives through partly is.

---

## 8. What remains uncalibrated

Ranked by how much the result moves with them:

| Assumption | Sensitivity | Status |
|---|---|---|
| **Vehicle mix** | **High** — 32.6 s swing in mean time loss | Unsourced. Top priority for field data. |
| **Signal programs** | Untested — would need alternative programs | netconvert defaults. Only 2 of 8 meaningfully control traffic. |
| **Free-flow speeds** | Untested — needs sourced speeds | 1,375 edges SUMO-supplied; 166 at ≥80 km/h |
| **Demand volume** | Bounded by capacity | Calibrated to the model, not to counts |
| `lcAssertive` | **Low** — 4.94 s, below seed noise | Preserved at 2.0 |
| `latAlignment` | **Zero** — sublane model not enabled | Inert. Documented, not changed. |
| Vehicle dimensions | Untested | Typical values, unsourced |

**The claim "the simulation matches Bengaluru" is not made and is not supported.**
What Phase 4 supports is narrower and real: this is a *simulation-based baseline*
built on *publicly sourced road geometry*, *estimated demand* and *estimated
vehicle behaviour*, whose internal variability is now quantified and whose
sensitivity to each major assumption is measured.
