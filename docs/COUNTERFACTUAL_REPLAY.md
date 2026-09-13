# Phase 5: counterfactual replay — seed 42

Results of running one identical scenario under four signal policies.

> ### Superseded in part — read [`archive/PHASE5A_CONTROL.md`](archive/PHASE5A_CONTROL.md) first
>
> This document records the **`signalised` trip** result and is kept unchanged as
> that record. Two things have happened since.
>
> **1. A measurement defect.** Section 1 below concludes from an empty
> `signal_wait_events` that the ambulance "never halts within 60 m of a traffic
> light on its route". **That inference does not follow.** The detector measured
> distance to the *start* of the approach edge, not the stop line, so once the
> ambulance was on that edge TraCI returned `INVALID_DOUBLE_VALUE` and the check
> could essentially never fire. It is fixed now. The headline of 0.00 s saved
> rests independently on the 3.5 s free-flow approach traversal and probably
> still stands, but this trip has **not** been re-run under the corrected
> detector, and the result files below retain the uncorrected field.
>
> **2. A better scenario.** The Phase 5a `two_signal` trip passes two actionable
> signals (green fractions 0.267 and 0.433) and the ambulance is genuinely
> stopped at a red. On it, the policies save 12.5 s.
>
> **Do not quote the traffic-side costs below as the cost of signal priority.**
> A control experiment showed the traffic-side effect of these perturbations
> reproduces with no ambulance in the network at all.

> **Simulated results.** Under this simulation scenario only. Not a measurement
> of real ambulance performance in Bengaluru, and not evidence about what signal
> priority would achieve there.

Machine-readable: [`counterfactual/`](../data/processed/silk_board_v1/counterfactual/)
· Policies: [`EMS_SIGNAL_POLICIES.md`](EMS_SIGNAL_POLICIES.md)

```bash
python scripts/run_counterfactual.py --seed 42
```

**Seeds 43–46 have not been run.** Seed 42 is reported first for review.

---

## 1. The headline result

**All four policies produced the same simulated ambulance travel time: 205.5 s.
Time saved: 0.00 s. Traffic-side cost: 17,270 to 33,575 vehicle-seconds.**

| Policy | Ambulance travel | Time saved | Traffic time loss Δ | Net system | Transitions |
|---|---|---|---|---|---|
| **NORMAL** | 205.5 s | — | — | — | 0 |
| **EMS_NEXT** | 205.5 s | **0.00 s** | **+17,270 s** | +17,270 s | 12 (3 signal changes) |
| **EMS_ROLLING** | 205.5 s | **0.00 s** | **+17,270 s** | +17,270 s | 22 (5 signal changes) |
| **EMS_FULL_PREEMPTION** | 205.5 s | **0.00 s** | **+33,575 s (+13.3%)** | +33,575 s | 47 (12 signal changes) |

Traffic figures exclude the ambulance. Including it changes nothing material —
one vehicle in ~2,900.

### Why the benefit is zero

**The ambulance is never stopped by a signal.** Across all four runs
`signal_wait_events` is empty: it never halts within 60 m of a traffic light on
its route. At the one actionable signal, `joinedS_`, its approach edge takes
**3.5 s in both NORMAL and EMS_FULL_PREEMPTION** — it arrives on green and drives
through.

A priority policy can only recover time a signal was taking. Here it was taking
none, so there is none to recover. The policies still *worked* — 12, 22 and 47
state transitions, greens held, one cross-traffic green truncated in each of
ROLLING and FULL — and every second of that intervention landed on cross traffic.

**This is a result, not a failure.** It is what the counterfactual method is for:
it distinguishes "the intervention helped" from "the intervention ran". A
single-arm study showing 205.5 s under EMS_FULL_PREEMPTION would have said
nothing about whether preemption did anything.

---

## 2. Cost of the intervention (EMS_FULL_PREEMPTION vs NORMAL)

| Metric (excluding the ambulance) | NORMAL | Policy | Δ |
|---|---|---|---|
| Vehicles completed | 2,909 | 2,896 | **−13** |
| Total time loss | 252,249 s | 285,824 s | **+33,575 s (+13.3%)** |
| Total waiting time | 165,720 s | 192,875 s | **+27,156 s (+16.4%)** |
| Mean time loss per vehicle | 86.7 s | 98.7 s | **+12.0 s (+13.8%)** |

Every vehicle that completed a trip lost on average 12 additional seconds so that
the ambulance could save none.

`net_system_impact` weights an ambulance-second the same as a car-second. That is
a modelling choice, not a policy judgement — whether an ambulance second is worth
more is a question this project cannot answer.

---

## 3. Scenario identity — what was held fixed

Every run used the same network, demand file, mix, seed, ambulance and route. The
identity is **computed and compared** for each pair, and a mismatch raises rather
than being reported with a caveat: a number from a mismatched pair looks exactly
like a policy effect.

Verified identical across all four runs: network SHA-256, demand config hash,
demand ID, seed, begin/end/step, vehicle mix, ambulance origin/destination/depart,
**ambulance route edges**, scenario variant.

The scenario hash deliberately excludes the policy. Two runs differing only in
policy hash identically, and that equality *is* the statement that they are the
same scenario.

**Route unchanged in all four runs** (16 edges). Had it differed, a travel-time
difference might have been a different path rather than the policy.

---

## 4. Validity and failures

| Check | NORMAL | EMS_NEXT | EMS_ROLLING | EMS_FULL |
|---|---|---|---|---|
| Ambulance completed | yes | yes | yes | yes |
| **Ambulance teleported** | **no** | **no** | **no** | **no** |
| **Signal conflicts** | **0** | **0** | **0** | **0** |
| Network teleports | 0 | 0 | 0 | **1** |
| Insertion backlog | 0 | 0 | 0 | 0 |
| Valid for headline | yes | yes | yes | yes |

The single teleport under EMS_FULL_PREEMPTION was `f021_motorcycle.131` at
t=3594 s on edge `27934854#7` — not the ambulance, and on a road not on its
route. It is reported rather than absorbed: that vehicle's travel time is not a
time anyone drove, and it is one of the 2,896 in the traffic aggregate.

A run in which the *ambulance* teleported or failed to arrive would be flagged
invalid for headline comparison. None did.

---

## 5. Did the four policies actually behave differently?

**Yes, operationally — and the numbers show where they stop differing.**

| | NORMAL | EMS_NEXT | EMS_ROLLING | EMS_FULL |
|---|---|---|---|---|
| State transitions | 0 | 12 | 22 | 47 |
| Signal changes logged | 0 | **3** | **5** | **12** |
| First activation | — | t=766.5 s, 242 m out | t=743.0 s, 691 m out | **t=600.5 s, 2,824 m out** |
| Traffic cost | — | +17,270 s | +17,270 s | **+33,575 s** |

EMS_NEXT and EMS_ROLLING converge on the same traffic cost here because only one
signal is actionable on this route — the window that distinguishes them has
nothing extra to coordinate. On a route with several actionable signals they
would separate. EMS_FULL_PREEMPTION is clearly distinct: it activates from
departure at 2.8 km out and costs roughly twice as much.

---

## 6. What this does not show

1. **Not that signal priority is ineffective.** It shows that on *this* route,
   under *this* demand, the ambulance was not delayed by signals, so there was
   nothing to recover. A route where it queues at a red would give a different
   answer.
2. **Not a Bengaluru result.** Demand, vehicle behaviour and signal programs are
   all estimated (Phase 4). The baseline these policies are measured against is
   assumed timing, not verified controller timing.
3. **Not yet an uncertainty statement.** One seed. Phase 4 put the ambulance
   seed-to-seed standard deviation at 1.14 s, so a 0.00 s difference is
   comfortably inside noise — but confirming that needs seeds 43–46.

### Why the route matters, and what changed

The Phase 3 ambulance trip produced a route through **zero** traffic lights — the
router sent it down a residential rat-run, which is also why its waiting time was
0.0 s in all five Phase 4 seeds. Signal priority is untestable on such a route.

Phase 5 selected a different destination by searching boundary pairs for a route
passing signals with red exposure. The Phase 3 trip is **unchanged**, so Phase 4's
published numbers still describe it. Even so, the new route yields only **one**
actionable signal, and the ambulance passes it on green.

**Getting a meaningful test of priority needs an ambulance that actually queues at
a signal.** That is a scenario-design question — heavier demand, a later
departure into built-up queues, or an origin-destination pair whose route meets
more actionable signals — and it should be settled deliberately rather than by
picking whichever variant produces a positive number.
