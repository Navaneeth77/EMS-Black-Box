# Phase 2.5: SUMO network review

Closing the open items from Phase 2 before the network is used for research.

**Status:** 6 items investigated. 3 resolved, 1 corrected in the conversion,
2 remain open and need a human. Validation: **29 checks, 0 errors, 6 warnings.**

Machine-readable: [`data/processed/silk_board_v1/network_review.json`](../data/processed/silk_board_v1/network_review.json)
· Provenance: [`data/provenance/sumo_network_review.json`](../data/provenance/sumo_network_review.json)

```bash
python scripts/build_sumo_network.py     # rebuild + revalidate + regenerate the review
python scripts/review_sumo_network.py    # render it for a person
python scripts/review_sumo_network.py --section tls
```

---

## Correction to the brief

The brief treats `way/457214835` as the "(u/c)" flyover. They are two different
ways, and the distinction matters because they need opposite responses:

| Way | What it actually is |
|---|---|
| `way/457214835` | **Sarjapura Road** — `highway=tertiary`, `frontage_road=yes`, `lanes=2`, `oneway=yes`, `surface=asphalt`. **Not** under construction. A frontage road. |
| `way/1351994264` | **The "(u/c)" flyover** — `highway=primary_link`, `bridge=viaduct`, `layer=1`, name *Ragigudda-Silk Board Integrated Flyover (u/c)*. |

Item 1 covers the second; item 2 covers the first.

---

## 1. Under-construction infrastructure — **UNRESOLVED, by design**

13 road ways carry a construction, proposed or `(u/c)` marker. 12 are settled by
OSM itself and are already absent from the drivable network. One is not.

### `way/1351994264` — the source contradicts itself

```
highway     = primary_link        ← a drivable classification
bridge      = viaduct
layer       = 1
name        = Ragigudda-Silk Board Integrated Flyover (u/c)   ← under construction
lanes       = 2
oneway      = yes
surface     = asphalt
smoothness  = excellent
check_date  = 2024-07-08
dual_carriageway = yes
```

There is no `construction=*` or `proposed=*` tag. OSM classifies it as an open
road and names it as an unfinished one.

**Evidence each way**

| For operational | Against operational |
|---|---|
| `highway=primary_link` — a drivable class | name marked `(u/c)` |
| `check_date=2024-07-08` — a mapper surveyed it | no `start_date`, unlike its parent |
| `surface=asphalt`, `smoothness=excellent` — surveyed attributes | last edited 2026-04-23 and *still* marked `(u/c)` |

For contrast, the parent way `886153772` *Ragigudda-Silk Board Integrated
Flyover* carries `start_date=2024-07-17`, `maxspeed=40`, `maxheight=4.5`,
`source:maxspeed=sign` — OSM records that one as opened.

### In the network

- SUMO edge **`1351994264`**: 287.9 m, `highway.primary_link`, 2 lanes, **80 km/h
  (a netconvert default)**. Its parent flyover is signed at 40 km/h in OSM.
- **Currently drivable.**
- **Excluding it would isolate edge `886153773`** — a 1,908 m primary
  carriageway. Verified: with the edge removed the network splits into 1,513 + 1
  edges, and there is no alternative route from *Silk Board Interchange* to it.

### Verdict

**UNRESOLVED.** This is not a gap the project may close by inference. Choosing
"open" adds a 288 m link that may not exist; choosing "closed" removes a 1.9 km
carriageway that may. Either choice would propagate into every travel time
crossing that part of the network, presented as sourced.

**Evidence needed** — dated, and from outside OSM:

1. Dated satellite or street-level imagery of the ramp between the Silk Board
   Interchange deck and the Ragigudda flyover carriageway, after 2026-04-23.
2. A BBMP or BMRCL statement of the opening date for this phase.
3. Failing both, a site observation.

Record the outcome as `VERIFIED_REAL_DATA` (site visit) or
`PUBLICLY_SOURCED_DATA` (published source), **with its date**, and rerun the
build. Until then Phase 3 must treat any route using edge `1351994264` as
conditional.

---

## 2. Sarjapura Road fragment — **RESOLVED: study-area clipping**

`way/457214835`, *Sarjapura Road*, 580 m, `highway=tertiary`,
`frontage_road=yes`.

**It is connected in the source OSM.** Rebuilding the connectivity graph from the
raw extract puts it in the main component, reachable along with 692 of 707
drivable ways. It connects through four ways at three shared nodes:

| Neighbour | Class | Survived conversion? |
|---|---|---|
| `way/778475534` | residential | no |
| `way/1281357396` | service | no |
| `way/1516931685` | service | no |
| `way/1516931686` | service | no |

**All four connection points sit within ~8 m of the study area's northern
boundary** (lat 12.92438–12.92440 against an edge at 12.924471). Three of the
four connecting ways were removed by `--keep-edges.in-geo-boundary`; the fourth
survived clipping but landed in the same isolated fragment, which
`--keep-edges.components 1` then pruned.

**Cause: study-area clipping. Not an OSM mapping gap, and not a real-world
disconnection.**

**Effect on routing:** no route can traverse it, because it is absent from the
converted network. Any Phase 3 demand or ambulance route referencing it would
fail to build. Given it is a frontage road 8 m inside the northern edge, that is
not material for trips through Silk Board — but it is a real consequence of the
Phase 1 boundary, recorded rather than suppressed.

**Not fixed by moving the boundary.** Enlarging the study area until a warning
disappears is how a boundary stops meaning anything. If a later phase needs this
corridor, it belongs in a new `area_id` with runs repeated.

---

## 3. Traffic lights — **CORRECTED, partially**

### The correction

At netconvert's default `--tls.join-dist` of 20 m, the **Sarjapura Road /
Madiwala Sarjapura Road junction came out as five independent traffic lights**,
with pairs **10.3 m and 10.8 m** apart. One physical intersection modelled as
five uncoordinated signals would force a Phase 5 EMS priority policy to actuate
five objects the real junction controls as one, and the recovered time it
measured would be partly an artefact of that split.

**`--tls.join-dist` raised to 60 m.** Those five became a single 24-link light.
Verified across `junctions.join-dist` 15–30 m and `tls.join-dist` 20–80 m: edge
count (1,515), junction count (664) and grade separation (8 plan-view crossings,
0 shared junctions) are unchanged. 80 m was rejected — it additionally absorbs a
single-link Hosur Road signal, is less clearly correct, and does not fix what
remains.

**Traffic lights: 12 → 8.**

### Mapping

18 OSM `highway=traffic_signals` nodes in the extract, 11 inside the study area.

| SUMO TLS | Links | Roads | OSM signal nodes ≤75 m | Assessment |
|---|---|---|---|---|
| `joinedS_12074449284_12074449289_cluster_…` | 24 | Sarjapura Rd, Madiwala Sarjapura Rd | 1837005138, 12074449280, 12074449291, 12074449292 | PLAUSIBLE |
| `GS_cluster_10282769895_10775075568_…` | 13 | Hosur Rd, Madiwala Sarjapura Rd | 494271106, 4703140386, 10282769898 | PLAUSIBLE |
| `494271080` | 3 | Outer Ring Road | 494271080, 2971089260, 3805003789 | PLAUSIBLE |
| `306594280` | 3 | Outer Ring Road | 306594280, 2971089260, 3805003789 | PLAUSIBLE |
| `3805003789` | 2 | Srinagar–Kanyakumari Hwy | 306594280, 494271080, 2971089260, 3805003789 | PLAUSIBLE |
| `2971089260` | **1** | (unnamed) | 4 nodes nearby | **IMPLAUSIBLE** |
| `11348815671` | **1** | Hosur Road | 11348815671 | **IMPLAUSIBLE** |
| `12611782078` | **1** | (unnamed) | 12611782078 | OUT_OF_SCOPE (outside the study area) |

### The 7 signal nodes that became non-controlling

netconvert reported 7 signal nodes controlling no links. Five are OSM signal
nodes with no traffic light within 75 m, **all outside the study area** — their
junctions were clipped away:

`13058420509` (420 m), `650141554` (463 m), `5942550424` (509 m),
`618252032` (712 m), `1613154921` (2,064 m).

The remainder became the single-link lights above.

### Still open: Silk Board itself is four traffic lights

The four lights on **Outer Ring Road / Srinagar–Kanyakumari Highway** — the Silk
Board junction — remain separate, spread over **76.6 m**:
`494271080`, `306594280`, `3805003789`, `2971089260` (9 links between them).

`--tls.join` did not merge them at 60 m or at 80 m, because their underlying
junctions form no cluster: they sit on separate carriageways. Merging them needs
`--junctions.join-dist` raised, which risks merging grade-separated structures —
the exact hazard of item 5. **Not attempted. Needs human judgement.**

This matters for Phase 5: an EMS preemption policy at Silk Board would have to
coordinate four traffic lights.

### No timings were invented

Signal *locations* come from OSM. All *programs* were generated by netconvert
from defaults and remain `ESTIMATED_DATA`. Nothing in this review changed them.

---

## 4. Flyover and ramp connectivity — **RESOLVED**

7 major elevated deck edges, 47 ramps. **0 suspicious cross-connections.**

### The brief's invariant needed correcting

The expected chain was `ground → ramp → elevated deck → ramp → ground`. That
holds for the Ragigudda / Double Decker complex. It does **not** describe the
Silk Board Flyover, which OSM models as **Hosur Road itself rising over the
junction and coming back down** — no ramp involved:

```
1155963376#2 (Hosur Rd, L0) → 40696222 (Silk Board Flyover, L1) → 133763765 (Hosur Rd, L0)
1167293940   (Hosur Rd, L0) → 1208112303 (Silk Board Flyover, L1) → 237025636 (Hosur Rd, L0)
```

Rather than force the data to fit, the check tests the invariant true of both:

> every connection of an elevated deck is to a ramp, to another elevated edge, or
> to a ground road it **meets end-to-end** — never to a road it **crosses over**.

That distinction is decided by geometry, not by names or road classes. An earlier
name-based version of this check produced three false positives on exactly the
touchdowns above, because "Silk Board Flyover" does not contain "Hosur Road".
`edges_cross_in_interior()` now intersects the two geometries and asks whether
any intersection point lies more than 8 m from both edges' endpoints.

### The Ragigudda / Double Decker chain

```
886153772 (Ragigudda flyover, L1, 2074 m)
    → 1299917514 (Ramp A, L1)
    → 519734960 (Double Decker, L1)   ← also fed by ramp 1299932382
    → 1299917512 (Double Decker, L1)
    → 1416769967 (Hosur Road, L0)     ← touchdown
```

### Boundary terminals — expected, not defects

| Edge | Length | Role |
|---|---|---|
| `886153772` Ragigudda flyover | 2,074 m | source (no incoming) |
| `886153773` | 1,908 m | sink (no outgoing) |
| `172853384#3` Hosur Road ramp | 17 m | sink |
| `684917326#0` | 83 m | source |

All four straddle the study-area boundary. In a clipped network these are valid
insertion and departure points.

---

## 5. Junction joining — **RESOLVED**

**30 clusters joined at 15 m.** netconvert now writes
`silk_board_v1.net.joined-junctions.xml` recording which junctions went into
which cluster, so the number is checkable rather than one to trust.

29 cluster-named nodes remain in the final network for those 30 joins. Checked
against the audit file: every join consumes its member nodes entirely (none of
the 60 member ids survives), and traffic-light joining subsequently merged two
cluster nodes into one. Both counts are recorded rather than reconciled away.

**0 clusters merge a grade separation. 0 major roads change grade through a
junction.**

### What "suspicious" had to mean

21 junctions have edges at more than one OSM layer. Flagging those would bury the
real cases, because **every bridge meets its own approaches** — that is correct
topology.

Two refinements were needed after the first version of this check produced false
positives:

1. **Severity keys on the elevated side.** The first version flagged junction
   `1309736834` as MAJOR because *Tank Bund Road* (`highway=secondary`) is
   present. But the elevated edge there is `way/1331889738` — an unnamed
   `highway=residential, layer=1` way. A major road at the junction is not the
   same as a major road changing grade.
2. **Spanning layers is only a merge if the edges cross.** Cluster
   `cluster_6182653338_6505307831` contains a layer-1 residential way and *2nd
   Cross Road* at layer 0. Geometry shows they meet end-to-end — a short bridge
   and its approach.

After both: **12 minor junctions** carry through-traffic at two levels, all
residential or tertiary ways tagged `layer=1` meeting their own approaches (e.g.
`way/256258847` is explicitly `bridge=yes, highway=tertiary, layer=1`), and
**9 junctions** are single-sided layer transitions. Reported, not hidden.

---

## 6. Defaulted lanes and speeds — **QUANTIFIED, not replaced**

All `ESTIMATED_DATA`. Every affected edge ID is listed in `network_review.json`
under `defaults.defaulted_edge_ids`.

| | Count |
|---|---|
| Total edges | 1,515 |
| **Fully OSM-sourced** | **126** |
| Lane count defaulted | **1,227** |
| Speed defaulted | **1,375** |

### Speed

| km/h | Defaulted | From OSM |
|---|---|---|
| 10 | 40 | — |
| 20–30 | — | 51 |
| 40 | — | 21 |
| 50 | 1,169 | 8 |
| 60 | — | 41 |
| 80 | 119 | 19 |
| 100 | 47 | — |

### Lanes

| Lanes | Defaulted | From OSM |
|---|---|---|
| 1 | 1220 | 166 |
| 2 | 7 | 76 |
| 3 | — | 45 |
| 4 | — | 1 |

### By road class

| Class | Edges | Lanes defaulted | Speed defaulted |
|---|---|---|---|
| residential | 1196 | 1073 | 1168 |
| tertiary | 108 | 48 | 90 |
| **trunk** | **54** | **0** | **0** |
| living_street | 40 | 40 | 40 |
| primary | 36 | 7 | 14 |
| trunk_link | 33 | 22 | 27 |
| secondary | 33 | 33 | 33 |
| primary_link | 14 | 3 | 2 |
| unclassified | 1 | 1 | 1 |

**Every trunk edge — the Hosur Road and Outer Ring Road carriageways — has both
its lane count and its speed from OSM.** The corridors that matter most are the
best mapped.

### The concern: 166 edges at ≥80 km/h by default

**119 edges at 80 km/h and 47 at 100 km/h**, from netconvert's built-in type map,
which is derived from European conventions. `highway.secondary` gets 100 km/h;
in Bengaluru that is an urban arterial. Affected named roads include *Madiwala
Sarjapura Road*, *Sarjapura Road* and *Tank Bund Road*.

Why this matters more than a wrong lane count: **free-flow speed is the reference
that delay is measured against.** A 100 km/h free-flow reference on a road that
runs at 30 makes every delay figure computed over it too large.

**Not replaced.** No source in this project supports a better value, and a guess
would be worse than a labelled default — it would look sourced. Options for
Phase 3, in order of preference:

1. Obtain signed speed limits (survey or published order) → `VERIFIED_REAL_DATA`
   / `PUBLICLY_SOURCED_DATA`.
2. Apply an explicit, documented urban speed assumption via a committed
   netconvert type file → `ESTIMATED_DATA` with recorded reasoning.
3. Leave as-is and report the inflated free-flow reference as a limitation on
   every delay figure.

---

## Validation summary

**29 checks, 0 errors, 6 warnings.** New in this phase:

| Check | Severity | Result |
|---|---|---|
| `non_operational_ways_excluded` | ERROR | PASS |
| `no_deck_cross_connections` | ERROR | PASS — 0 |
| `no_major_road_grade_merge` | ERROR | PASS — 0 major, 12 minor |
| `joined_clusters_do_not_merge_grade_separation` | ERROR | PASS — 0 |
| `operational_status_resolved` | WARNING | **FAIL — `way/1351994264`** |
| `disconnections_explained` | WARNING | PASS — clipping, not an OSM gap |
| `traffic_lights_plausible` | WARNING | **FAIL — 3 single-link lights** |
| `traffic_lights_not_fragmented` | WARNING | **FAIL — Silk Board is 4 lights** |
| `ramps_connected_at_both_ends` | WARNING | **FAIL — 2 boundary terminals** |
| `default_speeds_plausible` | WARNING | **FAIL — 166 edges ≥80 km/h** |
| `defaults_are_enumerated` | INFO | PASS |

---

## Is the network safe as the simulation source of truth?

**Yes for structure. Conditionally for measurement.**

**Structurally sound.** The flyover is grade-separated at all 8 of its plan-view
crossings, no junction merge collapsed a grade separation, no deck connects to a
road it crosses, the network is one connected component, SUMO loads it, and every
edge traces back to an OSM way.

**Three conditions on any number it produces:**

1. **`way/1351994264` is unresolved.** Routes crossing edge `1351994264` are
   conditional on a fact not yet established. Resolve before publishing any
   result that depends on that corridor.
2. **Free-flow speeds are inflated on 166 edges.** Any delay measured against
   them is an over-estimate until the speeds are sourced or an explicit
   assumption is documented.
3. **Silk Board is four traffic lights.** Phase 5 EMS priority must coordinate
   all four, and the design must not let the split become the thing being
   measured.

---

## Needs human inspection

| # | Item | Why |
|---|---|---|
| 1 | Is `way/1351994264` open to traffic? | Only unresolved fact. Dated imagery or a BBMP/BMRCL source. Blocks confident results on that corridor. |
| 2 | Should the four Silk Board traffic lights be one? | Needs junction-level joining, which risks merging grade separations. Affects Phase 5 directly. |
| 3 | Are the 3 single-link traffic lights real? | Artefacts of clipping. Probably harmless; confirm they are not needed. |
| 4 | Urban speed limits for the 166 defaulted edges | Sets the free-flow reference for every delay figure. |
| 5 | The 12 minor layer-1 residential ways | Assessed as short bridges meeting their approaches. Worth one look in netedit. |
| 6 | Boundary terminals | Confirm the 4 source/sink edges are where demand should enter and leave. |

```bash
"$SUMO_HOME/bin/netedit" simulation/sumo/silk_board_v1/silk_board_v1.net.xml
```
