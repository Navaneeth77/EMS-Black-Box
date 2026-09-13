# Phase 5: EMS signal policies

Four signal-control policies, how each one differs operationally, and how
priority is granted without ever creating a conflicting green.

> Simulated results throughout. Nothing here is a measurement of real ambulance
> performance in Bengaluru. No ambulance GPS trace, dispatch record or observed
> signal timing was used, because none was available.

Companions: [`archive/PHASE5A_CONTROL.md`](archive/PHASE5A_CONTROL.md) (**scenario index, the
detector correction, and the control experiment — start here**) ·
[`COUNTERFACTUAL_REPLAY.md`](COUNTERFACTUAL_REPLAY.md) (the original `signalised`
result) · [`EMS_DELAY_ATTRIBUTION.md`](EMS_DELAY_ATTRIBUTION.md) (per-intersection)

> The route analysis in section 4 describes the **`signalised`** trip. Two later
> trips exist; [`archive/PHASE5A_CONTROL.md`](archive/PHASE5A_CONTROL.md) section 1 tabulates all
> three. The policies themselves are unchanged across all of them.

---

## 1. The policies

| Policy | Signals acted on | Activation | Held until | Min green |
|---|---|---|---|---|
| **NORMAL** | none | never | — | — |
| **EMS_NEXT** | the next actionable one only | 250 m | ambulance passes | 5 s |
| **EMS_ROLLING** | every actionable one within the window | 700 m | ambulance passes each | 5 s |
| **EMS_FULL_PREEMPTION** | all actionable on the route | ambulance departs (distance-independent) | trip ends | **3 s** |

The differences are operational, not labels. EMS_ROLLING can hold several
signals at once where EMS_NEXT holds one; EMS_FULL_PREEMPTION activates
everything from departure and truncates cross-traffic greens harder. A test
asserts the four have distinct parameter signatures, because four labels over
identical behaviour would produce four identical results and a comparison that
measured nothing.

**EMS_FULL_PREEMPTION is not a proposal.** It exists to bound the achievable
benefit and to show what that bound costs the rest of the network.

---

## 2. How priority is granted — and why it cannot conflict

Every policy works by **selecting among the phases netconvert already
generated**. None writes a signal state string.

This is a structural guarantee, not a tested property. netconvert's phases are
internally conflict-free, so any sequence of them is conflict-free too. A policy
that synthesised its own state could grant two conflicting movements green at
once, and the only defence would be a checker that might have a gap.

To reach a priority phase, a policy does **not** jump:

1. If the current phase already serves the ambulance → **hold** it.
2. If the current phase is a **yellow or all-red interphase** → leave it alone.
   It is never truncated; that is the clearance time cross traffic is owed.
3. Otherwise → end the current green **once its minimum has elapsed**, and let
   the program advance through its own interphases.

So the signal always passes through the yellow and all-red the controller
designed. An instantaneous green-to-green switch cannot occur.

Releasing is the same in reverse: the policy stops extending and the program
resumes by itself. Cross traffic gets its designed transition rather than a snap
back.

### What counts as a signal change

Both mechanisms are logged, once per continuous episode:

* **Truncating** a conflicting green once its minimum has elapsed, so the program
  advances towards a phase serving the ambulance.
* **Holding** a phase that already serves the ambulance, extending it past its
  programmed duration.

The second is easy to overlook and is where most of the traffic-side cost comes
from. Logging only truncations made EMS_NEXT appear to impose 17,270 s of extra
delay with "0 signal changes".

### Validation

Every watched signal's state is compared against its program's phase set **on
every simulation step**. A state that is not one of the program's phases would
mean something wrote a signal directly. Across all four seed-42 runs:
**0 conflicts**.

---

## 3. State machine

```
NORMAL ──► REQUESTED ──► PRIORITY_ACTIVE ──► CLEARING ──► NORMAL
```

`REQUESTED` is where the yellow and minimum-green constraints are honoured — a
real controller cannot switch instantly. `REQUESTED → NORMAL` is permitted (an
abandoned request); `NORMAL → PRIORITY_ACTIVE` and `PRIORITY_ACTIVE → NORMAL`
are not, and an attempt raises rather than being silently recorded.

Every transition logs: simulation time, ambulance edge/lane/position/speed,
distance to the signal, the signal state and phase before and after, the policy,
the affected link indices, and the reason.

---

## 4. Which traffic lights are affected — and which are not

Phase 4 found that only 2 of the network's 8 traffic lights control conflicting
movements. Phase 5 does not assume all 8 need intervention: it works out which
are on the route and which of their **links** the ambulance uses.

A link counts only when **both** its from-edge and to-edge are consecutive on the
route. Matching the from-edge alone would preempt movements the ambulance never
makes — cost for no benefit.

### On the seed-42 ambulance route (16 edges)

| Traffic light | Ambulance links | Green fraction | Actionable? |
|---|---|---|---|
| `GS_cluster_10282769895_…` | `[4]` | **1.000** | **No** — permanently green |
| `joinedS_12074449284_…` | `[0, 1, 12]` | **0.600** | **Yes** — 40% red exposure |

**One actionable traffic light.** `GS_cluster` gives the ambulance's movement
green in all four of its phases, so no priority policy can improve it; the
policies leave it alone rather than logging a state transition against a signal
they did not change.

### The Silk Board cluster

The four independent Silk Board signals identified in Phase 2.5 are **not
merged**. Their topology is untouched: each remains its own `tlLogic` with its
own program, and the policy addresses whichever of them a route actually uses.
On the seed-42 route none of the four is on the path — the route passes
`GS_cluster` and `joinedS_` instead.

---

## 5. Constraints

| Constraint | Value | Why |
|---|---|---|
| Minimum green | 5 s (3 s for FULL) | A movement just released must not be immediately stopped; without it the policy could flicker the signal and the "cost" would be an artefact |
| Maximum priority hold | 60 s (600 s for FULL) | An ambulance stopped on its approach for another reason must not hold cross traffic indefinitely |
| Yellow / all-red | **never truncated** | Clearance time cross traffic is owed |
| Signal states | program phases only | Makes conflicts structurally impossible |

---

## 6. The ambulance is observed, never moved

Its edge, lane, position, speed and distance to each route signal are read from
SUMO through TraCI every step. Nothing writes to it. It has no speed bonus, no
`jmIgnoreFoe*` parameters, and no `moveToXY` call exists anywhere in the policy
code. Its movement emerges from the simulation exactly as any other vehicle's
does.

A policy that could nudge the ambulance would make "time saved" measure the
nudge.
