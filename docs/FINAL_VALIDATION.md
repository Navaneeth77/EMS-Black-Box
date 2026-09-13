# Final cross-seed validation

> **Simulated results.** Under this simulation scenario only — estimated
> demand, netconvert-generated signal timings, and a hypothetical incident
> declared by this project. Not a measurement of real ambulance performance
> or of real traffic in Bengaluru.

Scenario prefix `inc_two_signal` · seeds [42, 43, 44, 45, 46] · 
20/20 runs present.

## 1. Every run

| Seed | Policy | Travel (s) | Waiting (s) | Time loss (s) | Stops | Halts at red | Traffic queue max (m) | EMS transitions | Signal changes | Conflicts | Teleports | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 42 | NORMAL | 282.5 | 53.5 | 96.43 | 2 | 2 | 76.78 | 0 | 0 | 0 | 1 | valid |
| 42 | EMS_NEXT | 220.0 | 0.0 | 34.2 | 0 | 0 | 64.56 | 16 | 5 | 0 | 0 | valid |
| 42 | EMS_ROLLING | 204.0 | 0.0 | 17.88 | 0 | 0 | 70.32 | 16 | 5 | 0 | 1 | valid |
| 42 | EMS_FULL_PREEMPTION | 202.5 | 0.0 | 16.97 | 0 | 0 | 70.34 | 24 | 7 | 0 | 3 | valid |
| 43 | NORMAL | 286.0 | 57.5 | 100.07 | 2 | 1 | 76.75 | 0 | 0 | 0 | 0 | valid |
| 43 | EMS_NEXT | 217.5 | 0.0 | 31.27 | 0 | 0 | 59.65 | 16 | 5 | 0 | 0 | valid |
| 43 | EMS_ROLLING | 205.5 | 0.0 | 19.36 | 0 | 0 | 66.91 | 16 | 5 | 0 | 1 | valid |
| 43 | EMS_FULL_PREEMPTION | 204.0 | 0.0 | 17.79 | 0 | 0 | 66.86 | 24 | 7 | 0 | 4 | valid |
| 44 | NORMAL | 282.0 | 56.0 | 96.07 | 2 | 2 | 76.81 | 0 | 0 | 0 | 0 | valid |
| 44 | EMS_NEXT | 219.5 | 0.0 | 33.53 | 0 | 0 | 64.58 | 16 | 5 | 0 | 0 | valid |
| 44 | EMS_ROLLING | 204.5 | 0.0 | 18.9 | 0 | 0 | 66.87 | 16 | 5 | 0 | 1 | valid |
| 44 | EMS_FULL_PREEMPTION | 203.0 | 0.0 | 17.06 | 0 | 0 | 66.91 | 24 | 7 | 0 | 1 | valid |
| 45 | NORMAL | 287.0 | 59.5 | 101.19 | 2 | 1 | 76.77 | 0 | 0 | 0 | 2 | valid |
| 45 | EMS_NEXT | 218.5 | 0.0 | 32.93 | 0 | 0 | 52.15 | 16 | 5 | 0 | 1 | valid |
| 45 | EMS_ROLLING | 204.5 | 0.0 | 18.63 | 0 | 0 | 66.9 | 16 | 5 | 0 | 0 | valid |
| 45 | EMS_FULL_PREEMPTION | 204.0 | 0.0 | 18.0 | 0 | 0 | 66.92 | 24 | 7 | 0 | 1 | valid |
| 46 | NORMAL | 282.5 | 55.0 | 96.85 | 2 | 2 | 76.77 | 0 | 0 | 0 | 0 | valid |
| 46 | EMS_NEXT | 219.0 | 0.0 | 33.36 | 0 | 0 | 58.55 | 16 | 5 | 0 | 0 | valid |
| 46 | EMS_ROLLING | 204.5 | 0.0 | 18.44 | 0 | 0 | 69.46 | 16 | 5 | 0 | 0 | valid |
| 46 | EMS_FULL_PREEMPTION | 204.5 | 0.0 | 18.74 | 0 | 0 | 66.91 | 24 | 7 | 0 | 0 | valid |

## 2. Cross-seed summary

**NORMAL baseline travel time:** mean 284.0 s, median 282.5 s, sd 2.318 s, range 282.0–287.0 s (n=5).

| Policy | Mean saved (s) | Median | sd | Min | Max | Mean improvement (%) |
|---|---|---|---|---|---|---|
| EMS_NEXT | **65.1** | 63.5 | 3.13 | 62.5 | 68.5 | 22.917 |
| EMS_ROLLING | **79.4** | 78.5 | 2.074 | 77.5 | 82.5 | 27.955 |
| EMS_FULL_PREEMPTION | **80.4** | 80.0 | 2.074 | 78.0 | 83.0 | 28.307 |

Per-seed saving, so nothing is hidden inside a mean:

| Policy | seed 42 | seed 43 | seed 44 | seed 45 | seed 46 |
|---|---|---|---|---|---|
| EMS_NEXT | 62.5 | 68.5 | 62.5 | 68.5 | 63.5 |
| EMS_ROLLING | 78.5 | 80.5 | 77.5 | 82.5 | 78.0 |
| EMS_FULL_PREEMPTION | 80.0 | 82.0 | 79.0 | 83.0 | 78.0 |

### Traffic-side term — DIAGNOSTIC ONLY

| Policy | Mean Δ total time loss (s) | sd | Min | Max |
|---|---|---|---|---|
| EMS_NEXT | -4287.53 | 31007.499 | -45880.66 | 40450.89 |
| EMS_ROLLING | 5586.292 | 39079.141 | -47061.16 | 56868.16 |
| EMS_FULL_PREEMPTION | 17148.432 | 34942.582 | -32565.64 | 57348.51 |

Not a cost or benefit of signal priority. Two controls reproduced changes
of this size and sign with **no ambulance in the network**, and across
seeds the term has no stable sign. See `FINAL_RND_REPORT.md` section 18.

## 3. Intersection attribution — ranked by recoverable delay

| Traffic light | Mean recoverable delay (s) | Max | Observations |
|---|---|---|---|
| `GS_cluster_10282769895_10775075568_11964440742` | **48.5** | 52.5 | 15 |
| `joinedS_12074449284_12074449289_cluster_118099` | **0.0** | -0.0 | 15 |

Delay is attributed from the ambulance's own recorded traversal of each
signal's approach, comparing the paired policy run against NORMAL. A
signal is never charged delay for being near the route.

