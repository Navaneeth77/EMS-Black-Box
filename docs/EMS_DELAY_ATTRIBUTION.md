# Phase 5: intersection delay attribution — seed 42

Where the ambulance's time went on its route, and how much of any change is
attributable to a specific traffic light.

> **Simulated results.** These are delays in a simulation built on estimated
> demand, estimated vehicle behaviour and netconvert-generated signal programs.
> Nothing here establishes that they correspond to delay at the real junction.

Machine-readable:
[`counterfactual/seed42_comparisons.json`](../data/processed/silk_board_v1/counterfactual/seed42_comparisons.json)
(`intersection_attribution`)

---

## 1. Method

Per-edge traversal times are recorded for the ambulance in both the NORMAL run
and the policy run of the **same seed**. The approach edge of each traffic light
on the route carries that light's share of the difference.

Edges **not** controlled by a signal are reported separately as an unattributed
residual. A change there is traffic the ambulance met elsewhere, and crediting it
to the signal policy would overstate the effect.

Validation: the per-edge times sum to the total travel time exactly
(205.5 s = 205.5 s), so no time is lost or double-counted in the attribution.

---

## 2. Attribution table — seed 42

| Traffic light | Approach edge | Actionable | Baseline | EMS_FULL | Delay reduction | Transitions here |
|---|---|---|---|---|---|---|
| `GS_cluster_10282769895_…` | `464465165#3` | **No** (green 1.000) | 17.5 s | 17.5 s | **0.0 s** | 0 |
| `joinedS_12074449284_…` | `1411121774#0` | **Yes** (green 0.600) | 3.5 s | 3.5 s | **0.0 s** | 47 |
| — | *unattributed* | — | — | — | **0.0 s** | — |

Identical for EMS_NEXT and EMS_ROLLING.

### Reading it

**The ambulance spends 3.5 seconds on the approach to the only actionable
signal.** There is no queue there to clear and no red to preempt — it arrives on
green. 47 state transitions at that signal under EMS_FULL_PREEMPTION produced a
delay reduction of 0.0 s.

`GS_cluster` receives no transitions at all: the ambulance's movement is green in
all four of its phases, so the policies correctly leave it alone rather than
logging an intervention against a signal they did not change.

---

## 3. Where the ambulance's time actually goes

Total 205.5 s over 16 edges, of which 24.3 s is time loss and 7.5 s is waiting.

The largest single contributions are **not** at signals:

| Edge | Time | Signal? |
|---|---|---|
| `40696222` (Silk Board Flyover) | 34.5 s | no |
| `312063814#2` (Hosur Road, entry) | 25.0 s | no |
| `1411121774#1` (Sarjapura Road, exit) | 20.5 s | no |
| `239438610#0` | 18.0 s | no |
| `464465165#3` | 17.5 s | `GS_cluster` (permanently green) |

Its one stop and 7.5 s of waiting occur away from any traffic light — no
`signal_wait_events` were recorded in any of the four runs.

**So on this route the ambulance's delay is queueing and geometry, not signals.**
That is why every policy recovers 0.0 s, and it is a more useful finding than a
positive number would have been on a route chosen to produce one.

---

## 4. Vehicles affected

The attribution table does not count cross-traffic vehicles held at each signal
individually. The network-wide cost is reported in the paired comparison instead:
**+33,575 vehicle-seconds of additional time loss under EMS_FULL_PREEMPTION**,
spread across 2,896 completed trips (+12.0 s each on average), with 13 fewer
vehicles completing their trips within the window.

Attributing that cost per-signal would need per-junction cross-traffic
measurement under both arms, which the current edge-level output supports for
delay but not yet for a clean per-signal split.

---

## 5. Limitations carried forward

Everything in Phase 4's list still applies, and all of it is upstream of these
numbers:

- Vehicle mix is model-specified, not field-calibrated (the most sensitive
  assumption: 32.6 s swing in mean time loss).
- Demand scale is calibrated to the model's capacity, not to a measured count.
- **Signal programs are netconvert-generated**, so the baseline these policies are
  measured against is assumed timing, not verified controller timing. This bears
  directly on the result: the reason the ambulance meets no red may be that the
  generated programs give its movement more green than a real controller would.
- 1,375 of 1,515 edge speeds are SUMO defaults; 166 are ≥80 km/h. Time loss is
  measured against free-flow, so it is over-estimated on those edges.
- `way/1351994264` remains operational-status **UNRESOLVED**. The seed-42
  ambulance route does **not** use it.
- The sublane model is deliberately not enabled, so two-wheeler lateral behaviour
  is inert.
- Single seed. Phase 4 put the ambulance seed spread at sd 1.14 s.

**None of this may be presented as a measurement of actual ambulance performance
in Bengaluru.**
