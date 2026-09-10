# Phase 4: baseline uncertainty

How much the simulation-based baseline moves when only the random seed changes,
and what that does and does not tell us.

> **This measures the model's internal variability, not its agreement with
> Bengaluru.** The demand, the vehicle behaviour and the signal programs are the
> same estimated assumptions in all five runs. Running them five times does not
> make them observations.

Machine-readable:
[`data/processed/silk_board_v1/calibration/multi_seed_summary_include_unresolved.json`](../data/processed/silk_board_v1/calibration/multi_seed_summary_include_unresolved.json)
· per-run: [`baseline_runs/`](../data/processed/silk_board_v1/baseline_runs/)

```bash
python scripts/run_multi_seed_baseline.py            # seeds 42-46
python scripts/verify_determinism.py --seed 42
```

---

## 1. The headline result

**Five seeds — 42, 43, 44, 45, 46 — of an identical scenario.** 3,226 vehicles
loaded and departed in every run; 799 s total wall clock.

### Ambulance

| | mean | median | sd | min | max | range |
|---|---|---|---|---|---|---|
| **Travel time** | **145.90 s** | **146.00 s** | **1.140 s** | 144.00 | 147.00 | **3.00 s** |
| Waiting time | 0.00 s | 0.00 s | 0.000 s | 0.00 | 0.00 | 0.00 s |
| Time loss | 17.18 s | 17.39 s | 1.144 s | 15.27 | 18.36 | 3.09 s |

Per-seed travel time: 42 → 146.0 · 43 → 144.0 · 44 → 146.0 · 45 → 146.5 ·
46 → 147.0 s.

Percentiles (5 samples, indicative only): p25 146.0 · p50 146.0 · p75 146.5 ·
p90 146.8 s.

### What this means for Phase 5

**The baseline ambulance travel time varies by 3.0 s across seeds, with a
standard deviation of 1.14 s.**

That is the noise floor. A counterfactual that reports a recovered time of, say,
2 s has reported nothing: the baseline moves by more than that on its own. A
recovered time of 20 s would be roughly 17 standard deviations out and could not
plausibly be a seed effect.

This is the single most useful number Phase 4 produces, and it could not have
been obtained from one run.

### Route consistency

**All five seeds produced the identical 29-edge route.** The travel times are
therefore samples of *one journey* under different traffic draws, and can be
pooled. Had the route varied, a difference between runs might have been a
different path rather than different traffic, and the distribution above would
have been meaningless.

### Background traffic

| | mean | median | sd | CV | range |
|---|---|---|---|---|---|
| Mean travel time (all vehicles) | 264.14 s | 266.11 s | 11.916 | 0.045 | 32.98 s |
| Mean waiting time | 58.10 s | 59.48 s | 8.834 | 0.152 | 23.92 s |
| Mean time loss | 87.78 s | 89.95 s | 12.008 | 0.137 | 33.24 s |
| Vehicles arrived | 2,914.4 | 2,917 | 21.27 | 0.007 | 54 |
| Vehicles still running at end | 311.2 | 309 | 21.09 | 0.068 | 53 |

**The ambulance is far more stable than the traffic around it** — CV 0.008
against 0.137 for background time loss. Its route runs on high-capacity trunk
corridors, where a seed-to-seed change in the vehicle stream has less effect than
on the minor roads where most of the variability sits.

### Teleports and backlog

| Seed | 42 | 43 | 44 | 45 | 46 |
|---|---|---|---|---|---|
| Teleports | 0 | 1 | 0 | **4** | 0 |
| Backlog | 0 | 0 | 0 | 0 | 0 |

Mean 1.0, max 4 — all below the threshold of 10, so no run was rejected.
**Individual runs are not hidden:** seed 45 produced four teleports where three
seeds produced none, and that is visible rather than averaged away. Four
teleported vehicles out of 3,226 do not invalidate the run, but their own travel
times are not times anyone drove.

Insertion backlog was zero in every run: the network accepted all the demand
asked of it.

---

## 2. Determinism

**Verified: the same seed reproduces the same run.**

```bash
python scripts/verify_determinism.py --seed 42 --duration 600
```

| Level | Method | Result |
|---|---|---|
| Flow definitions | byte-for-byte SHA-256 | **identical** |
| Routes | content SHA-256, header excluded | **identical** |
| Simulation | 13 fields within 1e-6 relative | **equivalent** |

### A false alarm worth recording

The first version compared the routes file **byte-for-byte and failed.** The
files differed in exactly two lines: duarouter's generated timestamp and the
absolute output path. All 2,363 lines of actual routes were identical.

duarouter is deterministic; the check was wrong. It now hashes route content and
excludes the generated header — the flows file, which this project writes itself,
is still compared byte-for-byte.

The reason this is worth writing down: a check that cries wolf is worse than no
check, because it teaches whoever reads it to skip past the one that matters.

---

## 3. What the seed spread does not cover

The five runs differ **only** in the random stream. Everything else is held
fixed, which means the uncertainty in everything else does not appear in these
numbers at all:

| Source of uncertainty | In the spread? |
|---|---|
| Vehicle insertion and driver-behaviour randomness | **yes** |
| Demand flow rates | no — identical in all runs |
| Vehicle mix | no — identical in all runs |
| Two-wheeler lane-filtering parameters | no — identical in all runs |
| Signal programs | no — netconvert defaults in all runs |
| Free-flow speeds | no — SUMO-supplied on 1,375 edges in all runs |
| Road geometry | no — one OSM extract |
| Whether any of it resembles Bengaluru | **no — nothing here tests this** |

A standard deviation of 1.14 s on the ambulance travel time is **not** a
confidence interval on how long an ambulance takes to cross Silk Board. It is the
spread of one assumed scenario under different random draws.

The parameter uncertainties are addressed separately, by the sensitivity
experiments in [`BASELINE_CALIBRATION.md`](BASELINE_CALIBRATION.md), and those
effects are considerably larger than the seed spread.

---

## 4. Unresolved infrastructure across seeds

`way/1351994264` — the "(u/c)" flyover Phase 2.5 could not resolve — is
**included by default** and its use is counted in every run.

| Seed | 42 | 43 | 44 | 45 | 46 |
|---|---|---|---|---|---|
| Vehicles routing over it | 192 | 192 | 192 | 192 | 192 |
| **Ambulance uses it** | **no** | **no** | **no** | **no** | **no** |

**The ambulance route does not use the unresolved infrastructure in any seed.**
That is a materially useful finding: the headline ambulance travel time is *not*
conditional on the unverified fact. 192 background vehicles per run are, and
their contribution to the traffic the ambulance drives through is an indirect
dependency that remains.

The alternative is available as a scenario flag rather than an argument:

```bash
python scripts/run_multi_seed_baseline.py --variant exclude_unresolved
```

which builds a network variant with the edge removed — and, necessarily, the
1.9 km carriageway it is the sole connection to.

---

## 5. Language

Throughout this project:

- **publicly sourced road geometry** — OpenStreetMap, community-mapped
- **estimated demand** — flow rates and mix assumed by this project
- **estimated vehicle behaviour** — uncalibrated SUMO parameters
- **netconvert-generated signal programs** — not Bengaluru timings
- **simulation-based baseline** — what this phase produces
- **simulation bottlenecks** — not real-world bottlenecks

The claim *"the simulation matches Bengaluru"* is not made anywhere, and nothing
in this project supports it. No observation of Silk Board traffic has been used
at any stage.
