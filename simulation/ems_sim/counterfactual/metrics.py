"""Paired counterfactual metrics and per-intersection attribution.

Two rules govern every number here.

**Differences are always paired, and always against NORMAL of the same seed.**
Comparing a policy's average against NORMAL's average across seeds would mix the
policy effect with the seed-to-seed spread — which Phase 4 measured at 1.14 s
standard deviation for ambulance travel time. Pairing removes that entirely: both
runs saw the same random stream, so what is left is the intervention.

**Nothing is called "recovered" until it has been paired.** A raw travel time
from a policy run is just a travel time. It becomes time saved only relative to
its own NORMAL run.

Traffic-side cost is reported twice — including and excluding the ambulance —
because the ambulance is one vehicle out of thousands, and a system-cost figure
that quietly contained the beneficiary would flatter the policy.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from ems_sim.counterfactual.runner import PolicyRunResult

NOT_A_REAL_WORLD_CLAIM = (
    "Simulated result. Under this simulation scenario, with estimated demand, "
    "estimated vehicle behaviour and netconvert-generated signal programs, this "
    "policy changed simulated ambulance travel time by the stated amount relative "
    "to its paired NORMAL run. It is not a measurement of any real ambulance "
    "journey in Bengaluru."
)


@dataclass
class TrafficAggregate:
    """Network-side totals for one run."""

    vehicle_count: int
    total_travel_time_s: float
    total_waiting_time_s: float
    total_time_loss_s: float
    mean_travel_time_s: float | None
    mean_waiting_time_s: float | None
    mean_time_loss_s: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "vehicle_count": self.vehicle_count,
            "total_travel_time_s": round(self.total_travel_time_s, 2),
            "total_waiting_time_s": round(self.total_waiting_time_s, 2),
            "total_time_loss_s": round(self.total_time_loss_s, 2),
            "mean_travel_time_s": self.mean_travel_time_s,
            "mean_waiting_time_s": self.mean_waiting_time_s,
            "mean_time_loss_s": self.mean_time_loss_s,
        }


def traffic_aggregate(
    result: PolicyRunResult,
    exclude_ambulance: bool,
    *,
    exclude_teleported: bool = True,
    only: set[str] | None = None,
) -> TrafficAggregate:
    """Totals over completed trips, optionally excluding the ambulance.

    ``exclude_teleported`` drops trips SUMO moved rather than let drive: their
    duration, waiting and time loss describe a journey that did not happen.
    ``only`` restricts the aggregate to a given set of vehicle ids, which is how
    the paired common cohort is measured — the same vehicles in both arms.

    On its own this is still each arm's own set of survivors. See
    :func:`cohort_summary` and the ``cohort`` block of :func:`paired_comparison`
    for the view that is valid for a causal claim, and why.
    """
    trips = [
        trip
        for trip in result.measurements.trips.values()
        if trip.arrival_s is not None
        and not (exclude_ambulance and trip.vehicle_id == result._ambulance_id)
        and not (exclude_teleported and trip.teleported)
        and (only is None or trip.vehicle_id in only)
    ]

    def total(attribute: str) -> float:
        return sum(getattr(t, attribute) or 0.0 for t in trips)

    def mean(attribute: str) -> float | None:
        values = [getattr(t, attribute) for t in trips if getattr(t, attribute) is not None]
        return round(statistics.fmean(values), 3) if values else None

    return TrafficAggregate(
        vehicle_count=len(trips),
        total_travel_time_s=total("travel_time_s"),
        total_waiting_time_s=total("waiting_time_s"),
        total_time_loss_s=total("time_loss_s"),
        mean_travel_time_s=mean("travel_time_s"),
        mean_waiting_time_s=mean("waiting_time_s"),
        mean_time_loss_s=mean("time_loss_s"),
    )


def cohort_summary(result: PolicyRunResult) -> dict[str, Any]:
    """What became of every vehicle that entered, in one run.

    The live record is richer than SUMO's tripinfo: it holds an entry for every
    vehicle that departed, including those still running when the horizon cut the
    run off, together with the waiting time and time loss they had accumulated by
    then. Those totals are **censored** — the true figure is at least that — and
    are reported as such rather than either dropped or treated as final.
    """
    ambulance_id = result._ambulance_id
    trips = [t for t in result.measurements.trips.values() if t.vehicle_id != ambulance_id]
    completed = [t for t in trips if t.arrival_s is not None]
    unfinished = [t for t in trips if t.arrival_s is None]
    teleported = [t for t in trips if t.teleported]
    return {
        "departed_count": len(trips),
        "completed_count": len(completed),
        "completed_not_teleported_count": len([t for t in completed if not t.teleported]),
        "unfinished_count": len(unfinished),
        "teleport_count": len(teleported),
        "teleport_rate": round(len(teleported) / len(trips), 6) if trips else None,
        "never_departed_count": result.measurements.insertion_backlog_at_end,
        "censored_time_loss_s": round(sum(t.time_loss_s or 0.0 for t in unfinished), 2),
        "censored_waiting_time_s": round(sum(t.waiting_time_s or 0.0 for t in unfinished), 2),
        "censored_note": (
            "Accumulated by vehicles that had not finished when the run ended. A "
            "lower bound on the delay they suffered, not a total, and never added "
            "to a completed-vehicle figure."
        ),
        "ambulance_excluded": ambulance_id,
    }


def paired_cohort_view(normal: PolicyRunResult, policy: PolicyRunResult) -> dict[str, Any]:
    """The traffic-side comparison over vehicles that finished in **both** arms.

    Summing over each arm's own completers gives the two arms different
    denominators: a policy that leaves more traffic unfinished drops its own
    worst delays out of its own total and can look cheaper for being more
    expensive. This restricts both sides to the same vehicles, states how many
    that left out, and reports the asymmetry in completion as a result in its own
    right rather than as a rounding detail.
    """
    ambulance_id = normal._ambulance_id
    normal_trips, policy_trips = normal.measurements.trips, policy.measurements.trips
    departed_both = set(normal_trips) & set(policy_trips)
    departed_both.discard(ambulance_id)

    def clean(trips, ids):
        return {
            v
            for v in ids
            if trips[v].arrival_s is not None and not trips[v].teleported
        }

    normal_clean = clean(normal_trips, departed_both)
    policy_clean = clean(policy_trips, departed_both)
    common = normal_clean & policy_clean

    base = traffic_aggregate(normal, exclude_ambulance=True, only=common)
    other = traffic_aggregate(policy, exclude_ambulance=True, only=common)
    return {
        "paired_count": len(common),
        "departed_in_both": len(departed_both),
        "departed_only_in_normal": len(set(normal_trips) - set(policy_trips)),
        "departed_only_in_policy": len(set(policy_trips) - set(normal_trips)),
        "completed_only_in_normal": len(normal_clean - policy_clean),
        "completed_only_in_policy": len(policy_clean - normal_clean),
        "normal": base.as_dict(),
        "policy": other.as_dict(),
        "deltas": {
            "total_time_loss_s": _delta(base.total_time_loss_s, other.total_time_loss_s),
            "mean_time_loss_s": _delta(base.mean_time_loss_s, other.mean_time_loss_s),
            "total_waiting_time_s": _delta(base.total_waiting_time_s, other.total_waiting_time_s),
            "mean_waiting_time_s": _delta(base.mean_waiting_time_s, other.mean_waiting_time_s),
        },
        "note": (
            "The same vehicles in both arms, each completing its route without a "
            "teleport in either. Vehicles that finished in only one arm are "
            "excluded and counted above; a policy that finishes fewer vehicles "
            "has not made the network faster."
        ),
    }


def _delta(baseline: float | None, policy: float | None) -> dict[str, Any] | None:
    if baseline is None or policy is None:
        return None
    change = policy - baseline
    return {
        "normal": round(baseline, 3),
        "policy": round(policy, 3),
        "absolute": round(change, 3),
        "percent": round(100.0 * change / baseline, 3) if baseline else None,
    }


def paired_comparison(normal: PolicyRunResult, policy: PolicyRunResult) -> dict[str, Any]:
    """Compare one policy run against its paired NORMAL run of the same seed."""
    if normal.seed != policy.seed:
        raise ValueError(
            f"Cannot pair seed {normal.seed} with seed {policy.seed}. Different "
            f"seeds are different traffic, not a counterfactual pair."
        )

    normal_valid, normal_reason = normal.valid_for_headline
    policy_valid, policy_reason = policy.valid_for_headline

    normal_trip, policy_trip = normal.ambulance, policy.ambulance
    ambulance = {
        "travel_time": _delta(
            normal_trip.travel_time_s if normal_trip else None,
            policy_trip.travel_time_s if policy_trip else None,
        ),
        "waiting_time": _delta(
            normal_trip.waiting_time_s if normal_trip else None,
            policy_trip.waiting_time_s if policy_trip else None,
        ),
        "time_loss": _delta(
            normal_trip.time_loss_s if normal_trip else None,
            policy_trip.time_loss_s if policy_trip else None,
        ),
        "stops": _delta(float(normal.ambulance_stop_count), float(policy.ambulance_stop_count)),
    }
    travel = ambulance["travel_time"]
    time_saved = -travel["absolute"] if travel else None

    traffic = {}
    for label, exclude in (("excluding_ambulance", True), ("including_ambulance", False)):
        base = traffic_aggregate(normal, exclude)
        cf = traffic_aggregate(policy, exclude)
        traffic[label] = {
            "normal": base.as_dict(),
            "policy": cf.as_dict(),
            "deltas": {
                "vehicles_completed": _delta(float(base.vehicle_count), float(cf.vehicle_count)),
                "total_time_loss_s": _delta(base.total_time_loss_s, cf.total_time_loss_s),
                "total_waiting_time_s": _delta(base.total_waiting_time_s, cf.total_waiting_time_s),
                "mean_time_loss_s": _delta(base.mean_time_loss_s, cf.mean_time_loss_s),
            },
        }

    cohort = {
        "normal": cohort_summary(normal),
        "policy": cohort_summary(policy),
        "paired": paired_cohort_view(normal, policy),
        "note": (
            "The counts come first because the metric below depends on them: "
            "`traffic` sums over each arm's own completers, which are different "
            "vehicles, and `paired` sums over the vehicles both arms finished."
        ),
    }
    cohort["unfinished_delta_policy_minus_normal"] = (
        cohort["policy"]["unfinished_count"] - cohort["normal"]["unfinished_count"]
    )

    traffic_cost = traffic["excluding_ambulance"]["deltas"]["total_time_loss_s"]
    traffic_cost_s = traffic_cost["absolute"] if traffic_cost else None

    return {
        "seed": normal.seed,
        "baseline_policy": normal.policy,
        "counterfactual_policy": policy.policy,
        "both_runs_valid_for_headline": normal_valid and policy_valid,
        "validity": {
            "normal": {"valid": normal_valid, "reason": normal_reason},
            "policy": {"valid": policy_valid, "reason": policy_reason},
        },
        "ambulance": ambulance,
        "ambulance_time_saved_s": time_saved,
        "cohort": cohort,
        "ambulance_improvement_percent": (
            round(-travel["percent"], 3) if travel and travel["percent"] is not None else None
        ),
        "traffic": traffic,
        "traffic_delay_cost_s": traffic_cost_s,
        "traffic_metric_status": "DIAGNOSTIC_ONLY",
        "traffic_metric_cohort": (
            "Each arm's own completers, excluding teleported trips. These are "
            "DIFFERENT VEHICLES in the two arms. Use comparison['cohort']['paired'] "
            "for the same-vehicles view."
        ),
        "traffic_metric_warning": (
            "NOT a cost or benefit of signal priority, and must not be reported as "
            "one. Two controls (Phase 5a replay, Phase 5b fixed schedule) reproduced "
            "traffic-side changes of this size and sign with NO ambulance in the "
            "network at all — in Phase 5b, 24.5-72.0 s of signal holding moved total "
            "time loss by 19,555-47,918 vehicle-seconds. The metric is dominated by "
            "the chaotic response of netconvert's unoptimised fixed-time plans to "
            "being perturbed, not by the ambulance. Retained as a diagnostic of that "
            "sensitivity. See docs/archive/FINAL_RND_REPORT.md section 18."
        ),
        "net_system_impact_s": (
            round(traffic_cost_s - (time_saved or 0.0), 3) if traffic_cost_s is not None else None
        ),
        "net_system_impact_status": "DIAGNOSTIC_ONLY",
        "net_system_impact_note": (
            "DIAGNOSTIC ONLY — inherits the traffic term above, which the controls "
            "showed is not attributable to the ambulance. Do not quote as a net "
            "benefit or cost of priority. "
            "Traffic-side time loss added, minus ambulance time saved, in "
            "vehicle-seconds. Positive means the network as a whole lost more than "
            "the ambulance gained. This weights one ambulance-second the same as "
            "one car-second, which is a modelling choice, not a policy judgement: "
            "whether an ambulance second is worth more is a question this project "
            "cannot answer."
        ),
        "route_unchanged": normal.ambulance_route_edges == policy.ambulance_route_edges,
        "teleports": {
            "normal": len(normal.measurements.teleports),
            "policy": len(policy.measurements.teleports),
        },
        "signal_conflicts": {
            "normal": len(normal.signal_conflicts),
            "policy": len(policy.signal_conflicts),
        },
        "data_class": "SIMULATED_DATA",
        "interpretation": NOT_A_REAL_WORLD_CLAIM,
    }


class RouteChangedError(RuntimeError):
    """The two runs did not drive the same route, so nothing can be attributed."""


def upstream_queue_attribution(
    normal: PolicyRunResult, policy: PolicyRunResult
) -> dict[str, Any]:
    """Delay on the edges *behind* a signal, attributed to that signal — with evidence.

    An ambulance held 200 m back in a queue is being held by the signal at the
    head of that queue, but the delay is recorded against the edge it is standing
    on, which is not the signal's approach. Attributing only the approach edge
    therefore credits a priority policy with the smaller half of what it does.

    The rule, and it is deliberately conservative:

    * take each route edge that is not itself a signal approach;
    * find the next signal ahead of it on the route;
    * work out when the ambulance was on that edge, by accumulating the recorded
      per-edge traversal times from its departure;
    * attribute the edge's delay change to that signal **only if** a queue was
      recorded standing on that signal's approach during that window.

    Where there is no queue evidence the change stays unexplained, and is added
    to the unattributed bucket rather than to a signal. The window is an
    approximation — it accumulates edge times and so ignores the seconds spent on
    junction internal lanes — and is stated here rather than presented as exact.
    """
    approaches = {entry["approach_edge"] for entry in policy.route_tls}
    next_signal: dict[str, dict[str, Any]] = {}
    for index, edge_id in enumerate(policy.ambulance_route_edges):
        ahead = [
            entry
            for entry in policy.route_tls
            if entry.get("route_index", -1) >= index and entry["approach_edge"] != edge_id
        ]
        if ahead:
            next_signal[edge_id] = min(ahead, key=lambda e: e["route_index"])

    def entry_times(result: PolicyRunResult) -> dict[str, tuple[float, float]]:
        """When the ambulance was on each route edge, from its own traversal times."""
        trip = result.ambulance
        clock = (trip.depart_s if trip and trip.depart_s is not None else 0.0)
        spans: dict[str, tuple[float, float]] = {}
        for edge_id in result.ambulance_route_edges:
            duration = result.ambulance_edge_times.get(edge_id)
            if duration is None:
                continue
            spans[edge_id] = (clock, clock + duration)
            clock += duration
        return spans

    normal_spans = entry_times(normal)
    edges: list[dict[str, Any]] = []
    unexplained: list[dict[str, Any]] = []
    for edge_id in policy.ambulance_route_edges:
        if edge_id in approaches:
            continue
        base = normal.ambulance_edge_times.get(edge_id)
        cf = policy.ambulance_edge_times.get(edge_id)
        if base is None or cf is None:
            continue
        change = cf - base
        signal = next_signal.get(edge_id)
        span = normal_spans.get(edge_id)
        queue_series = normal.queue_by_tls.get(signal["tls_id"], []) if signal else []
        standing = 0
        if span and queue_series:
            during = [row for row in queue_series if span[0] <= row[0] <= span[1]]
            standing = int(max((row[1] for row in during), default=0))
        if signal is not None and standing > 0:
            edges.append(
                {
                    "edge_id": edge_id,
                    "attributed_to_tls_id": signal["tls_id"],
                    "attributed_to_encounter": signal.get("key", signal["tls_id"]),
                    "baseline_traversal_time_s": round(base, 2),
                    "counterfactual_traversal_time_s": round(cf, 2),
                    "delay_reduction_s": round(-change, 2),
                    "queue_at_that_signal_while_here": standing,
                    "ambulance_on_this_edge_s": [round(span[0], 1), round(span[1], 1)],
                }
            )
        else:
            unexplained.append(
                {
                    "edge_id": edge_id,
                    "delay_change_s": round(change, 2),
                    "reason": (
                        "no queue was recorded at the next signal while the ambulance "
                        "was on this edge"
                        if signal is not None
                        else "no signal ahead of this edge on the route"
                    ),
                }
            )

    return {
        "edges": edges,
        "unexplained": unexplained,
        "rule": (
            "Delay on a non-approach route edge is attributed to the next signal "
            "ahead of it only when a queue was recorded standing at that signal "
            "while the ambulance was on the edge. Otherwise it is unattributed."
        ),
        "window_basis": (
            "Accumulated per-edge traversal times from the ambulance's departure in "
            "the NORMAL run. Approximate: time on junction internal lanes is not "
            "included in the per-edge record."
        ),
    }


def intersection_attribution(normal: PolicyRunResult, policy: PolicyRunResult) -> dict[str, Any]:
    """Attribute the ambulance's delay change to individual traffic lights.

    Per-edge traversal times are compared between the paired runs, and the
    approach edge of each traffic light on the route carries that light's share.

    Three properties this has to have, and did not:

    * **The routes must be identical.** Comparing per-edge times between two
      different routes attributes the difference between two journeys to a
      signal. It now raises rather than reporting a number.
    * **No traffic light may be dropped.** The approach edges were used as dict
      keys, so if two signals shared one — or one signal appeared twice, as it
      does whenever the route makes two controlled movements through the same
      junction — one silently replaced the other and its delay went into the
      unattributed bucket. Encounters are now kept as a list.
    * **Delay upstream of a signal is still the signal's.** Most of what a
      priority policy saves is not waiting at the stop line; it is the queue
      *behind* the stop line, which stands on the edges before the approach. That
      is reported as ``upstream_queue_delay``, separately from the delay measured
      on the approach itself, and only where there is evidence for it — a queue
      recorded at that signal while the ambulance was on that edge.
    """
    if normal.ambulance_route_edges != policy.ambulance_route_edges:
        raise RouteChangedError(
            f"{normal.policy} and {policy.policy} drove different routes, so a "
            f"per-edge difference between them is not a signal's doing. "
            f"{len(normal.ambulance_route_edges)} vs "
            f"{len(policy.ambulance_route_edges)} edges."
        )

    by_approach: dict[str, list[dict[str, Any]]] = {}
    for entry in policy.route_tls:
        by_approach.setdefault(entry["approach_edge"], []).append(entry)

    rows: list[dict[str, Any]] = []
    unattributed = 0.0
    for edge_id in policy.ambulance_route_edges:
        base = normal.ambulance_edge_times.get(edge_id)
        cf = policy.ambulance_edge_times.get(edge_id)
        if base is None or cf is None:
            continue
        change = cf - base
        entries = by_approach.get(edge_id)
        if not entries:
            # Not a signal approach. Handled by the upstream-queue rule below,
            # which either attributes it with evidence or leaves it unexplained;
            # counting it here as well would double it.
            continue
        # One edge, several controlled movements: the change on it is shared
        # rather than counted once per movement, which would inflate the total.
        share = change / len(entries)
        for entry in entries:
            rows.append(
                {
                    "tls_id": entry["tls_id"],
                    "encounter": entry.get("key", entry["tls_id"]),
                    "approach_edge": edge_id,
                    "actionable": entry["has_red_exposure"],
                    "green_fraction_for_ambulance": entry["green_fraction_for_ambulance"],
                    "baseline_traversal_time_s": round(base, 2),
                    "counterfactual_traversal_time_s": round(cf, 2),
                    "delay_reduction_s": round(-share, 2),
                    "movements_sharing_this_edge": len(entries),
                    "policy": policy.policy,
                }
            )

    for row in rows:
        report = policy.policy_report
        row["vehicles_affected_note"] = (
            "Cross-traffic vehicles held at this signal are not counted "
            "individually; the network-wide traffic cost is reported in the paired "
            "comparison."
        )
        row["state_transitions_here"] = sum(
            1
            for transition in report.get("state_transitions", [])
            if transition["tls_id"] == row["tls_id"]
        )

    upstream = upstream_queue_attribution(normal, policy)
    for row in rows:
        row["upstream_queue_delay_reduction_s"] = round(
            sum(
                u["delay_reduction_s"]
                for u in upstream["edges"]
                if u["attributed_to_tls_id"] == row["tls_id"]
            )
            / max(1, sum(1 for r in rows if r["tls_id"] == row["tls_id"])),
            2,
        )
    attributed_upstream = sum(u["delay_reduction_s"] for u in upstream["edges"])
    unattributed = sum(u["delay_change_s"] for u in upstream["unexplained"])

    return {
        "policy": policy.policy,
        "seed": policy.seed,
        "label": "simulated intersection attribution",
        "direct_signal_delay_reduction_s": round(sum(r["delay_reduction_s"] for r in rows), 2),
        "upstream_queue_delay_reduction_s": round(attributed_upstream, 2),
        "upstream_queue_attribution": upstream,
        "not_a_real_world_claim": (
            "These are delays in a simulation built on estimated demand and "
            "netconvert-generated signal programs. Nothing here establishes that "
            "they correspond to delay at the real junction."
        ),
        "intersections": sorted(rows, key=lambda r: -r["delay_reduction_s"]),
        "unattributed_change_s": round(-unattributed, 2),
        "unattributed_note": (
            "Change on route edges not controlled by a traffic light. Not "
            "attributable to the signal policy — it is traffic the ambulance met "
            "elsewhere, and crediting it to the policy would overstate the effect."
        ),
        "data_class": "SIMULATED_DATA",
    }
