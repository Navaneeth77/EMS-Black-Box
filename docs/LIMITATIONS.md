# Limitations

What this project's numbers cannot be used for. Read this before quoting any of
them.

---

## 1. There is no real ambulance in it

**No ambulance GPS trace, dispatch record or response-time log was available for
Bengaluru, and none is simulated as if it were.** The ambulance's origin,
destination and departure time are configuration choices made by this project to
put it through the junction under study. Every travel time here is the travel
time of a simulated vehicle in a simulated network.

Nothing in this repository establishes what signal priority would do for a real
ambulance at Central Silk Board. It establishes what it does to *this model*, and
the model's inputs are listed below with their sources.

## 2. The result is conditional, and the condition matters more than the headline

Signal priority is worth very different amounts depending on whether the
ambulance is already moving:

- **Free-flowing approach:** ~12 s saved over a ~185 s trip.
- **With the modelled queue-producing obstruction:** ~65–80 s, because the
  ambulance is stopped in a queue that priority can discharge.

Quoting the larger number alone would describe a scenario the smaller one
contradicts. The honest statement is that priority buys little when the corridor
is clear and much more when it is not — which is also the least surprising
possible finding, and the one the design can actually support.

**The same condition governs the differences between the policies.** In free flow
the three EMS policies are indistinguishable from each other: the undisturbed
ambulance makes exactly one signal stop, all three remove it, and the contrasts
between them sit inside the seed-to-seed noise. Any statement that a wider
look-ahead window is worth a particular number of seconds is a statement about
the congested scenario and must carry that condition. See
[`RESULTS.md`](RESULTS.md) §3.

## 3. One network, one junction pair, one trip, five seeds

The counterfactual matrix is 5 seeds × 5 policies on **one** ambulance route
through **two** actionable traffic lights in **one** OSM extract of **one**
junction. Seed-to-seed spread is small (≈1–3 s on ambulance travel time), which
says the simulation is stable — not that the finding generalises to another
route, another junction, or another city. It does not address:

- different times of day, or any demand not derived from the one observed count;
- other emergency vehicle types, or more than one at a time;
- any real controller's actual behaviour (see §5);
- what happens when priority is granted often enough for drivers to adapt.

## 4. The traffic-side metric is a diagnostic, not a cost

The network-wide delay figures **must not** be quoted as the cost of priority.
Three independent lines of evidence:

- **Phase 5a:** removing the ambulance entirely reproduced traffic-side changes
  of the same size and sign.
- **Phase 5b:** a fixed-schedule perturbation with no ambulance logic moved total
  time loss by 19,555–47,918 vehicle-seconds.
- **The 2026-09-14 cohort re-analysis:** on the corrected paired cohort, no
  policy's traffic-side effect is distinguishable from zero across five seeds
  (standard errors of 7–9 s per vehicle against means of −1 to +7).

What the metric is measuring is the chaotic response of netconvert's unoptimised
fixed-time plans to being perturbed at all. It is retained as a diagnostic of
that sensitivity, and every report labels it `DIAGNOSTIC_ONLY`.

## 5. The signal programs are not the real ones

netconvert generated them from OSM geometry. Nobody has the Bengaluru Traffic
Police's actual timings for these junctions.

The one sourced figure is the **450 s existing cycle length** at Central Silk
Board (IJIRSET 2017, p. 10540, verified against the PDF). It is used by the
HISTORICAL_DEMO four-way and by nothing else; its phase splits, amber, all-red
and phase order are `ESTIMATED` because no published source gives them.

**No result in this repository is a test of historically sourced signal timing.**
A 450 s counterfactual was examined and rejected: the source publishes a cycle
length and no phase information at all, and the junction it describes is one the
research ambulance never passes — its route's two signals are 510 m and 1,011 m
away, on different roads. Running it would have required inventing every phase
split and transplanting one junction's cycle onto two others.
[`METHOD.md`](METHOD.md) §3 gives the full reasoning.

A priority policy's value depends on the plan it is preempting. These are
plausible plans, not the installed ones — and how much a plan can be improved by
preemption is a property of that plan.

## 6. What the demand is, and is not

The demand is **derived** from one observed figure — 22,634 vehicles in the Silk
Board peak hour (CMP 2019, Table 2-13, itself citing a Dec 2014 – Apr 2015 survey)
— scaled by a factor `k` this project chose, and spread over origin-destination
pairs by weights this project chose. The vehicle mix is `ESTIMATED`. No turning
movement counts were available.

The HISTORICAL_DEMO runs at `k = 0.25`: a quarter of the observed peak hour.
Denser levels produced longer queues *and* vehicles teleporting out of gridlock,
and the scenario rule rejects any level that teleports.

## 7. Vehicles that never finish

Roughly 10% of vehicles are still in the network when a run's horizon ends. Their
delay is **censored**: known to be at least what they had accumulated, and never
completed. They are counted in every report, excluded from completed-vehicle
metrics, and their asymmetry between arms is itself reported — a policy that
finishes fewer vehicles has not made the network faster. What the censored delay
*totals* would have been is not estimated.

## 8. Teleports

SUMO teleports a vehicle that has been stuck too long. A teleported vehicle did
not drive the distance it is credited with, so its travel time is excluded from
travel-time metrics — but the teleport count and rate are reported per arm, and a
run whose **ambulance** teleported is invalid for headline use. Teleport rates in
the reported matrix are 0–4 vehicles per 3,226.

## 8a. The congestion sweep is three points on one mechanism

The severity sweep varies one number — how fast the obstructed lane discharges —
on one obstruction, on one lane, of one edge, at one time of day. It establishes
whether the benefit depends on queue severity *within that mechanism*. It does
not cover a different kind of disturbance (a full closure, an incident elsewhere
on the route, an incident on a cross approach, several at once), a different
demand level, or an obstruction the ambulance meets at a different point in its
trip. Three severities over five seeds is enough to see a trend and not enough to
fit one.

The severities themselves are `ASSUMED`. Nothing observed says an obstruction at
Silk Board discharges at 2.4, 0.6 or 0.15 m/s.

## 9. The 3D replay is a renderer, and the demo is not the research

The scene draws a recording. It has no opinion about what happened. But the
HISTORICAL_DEMO scenario — its trip, its demand scale, its four-way controller —
was chosen to be *watchable*: an ambulance visibly caught in a queue. Two of the
trip rule's five steps are about whether the network can be drawn without
vehicles overlapping, which is a legitimate constraint on a visualisation and not
one a traffic study would impose. **Demo numbers are not research numbers**, and
every demo artefact says so.

## 10. What the audit changed, and what that implies

The 2026-09-14 audit found measurement defects that changed published numbers
(see [`RESULTS.md`](RESULTS.md) §4). The relevant limitation is not any single
correction — it is that a repository can carry a defect for a whole phase while
every test passes, because the tests were written from the same understanding as
the code. The corrections here were found by re-deriving results from raw SUMO
output along a different path, not by the existing suite.

Where a corrected number is available it is the one reported. Where a frozen
number has not been recomputed, it is labelled as belonging to the earlier code
path rather than quietly carried forward.
