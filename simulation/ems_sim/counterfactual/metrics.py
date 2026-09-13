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


def traffic_aggregate(result: PolicyRunResult, exclude_ambulance: bool) -> TrafficAggregate:
    """Totals over completed trips, optionally excluding the ambulance."""
    trips = [
        trip
        for trip in result.measurements.trips.values()
        if trip.arrival_s is not None
        and not (exclude_ambulance and trip.vehicle_id == result._ambulance_id)
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
        "ambulance_improvement_percent": (
            round(-travel["percent"], 3) if travel and travel["percent"] is not None else None
        ),
        "traffic": traffic,
        "traffic_delay_cost_s": traffic_cost_s,
        "traffic_metric_status": "DIAGNOSTIC_ONLY",
        "traffic_metric_warning": (
            "NOT a cost or benefit of signal priority, and must not be reported as "
            "one. Two controls (Phase 5a replay, Phase 5b fixed schedule) reproduced "
            "traffic-side changes of this size and sign with NO ambulance in the "
            "network at all — in Phase 5b, 24.5-72.0 s of signal holding moved total "
            "time loss by 19,555-47,918 vehicle-seconds. The metric is dominated by "
            "the chaotic response of netconvert's unoptimised fixed-time plans to "
            "being perturbed, not by the ambulance. Retained as a diagnostic of that "
            "sensitivity. See docs/FINAL_RND_REPORT.md section 18."
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


def intersection_attribution(normal: PolicyRunResult, policy: PolicyRunResult) -> dict[str, Any]:
    """Attribute the ambulance's delay change to individual traffic lights.

    Per-edge traversal times are compared between the paired runs, and the
    approach edge of each traffic light on the route carries that light's share.
    Edges not controlled by a signal are reported separately: a change there is
    not attributable to the policy, and lumping it in would credit the policy with
    traffic noise.
    """
    tls_by_approach = {entry["approach_edge"]: entry for entry in policy.route_tls}

    rows: list[dict[str, Any]] = []
    unattributed = 0.0
    for edge_id in policy.ambulance_route_edges:
        base = normal.ambulance_edge_times.get(edge_id)
        cf = policy.ambulance_edge_times.get(edge_id)
        if base is None or cf is None:
            continue
        change = cf - base
        entry = tls_by_approach.get(edge_id)
        if entry is None:
            unattributed += change
            continue
        rows.append(
            {
                "tls_id": entry["tls_id"],
                "approach_edge": edge_id,
                "actionable": entry["has_red_exposure"],
                "green_fraction_for_ambulance": entry["green_fraction_for_ambulance"],
                "baseline_traversal_time_s": round(base, 2),
                "counterfactual_traversal_time_s": round(cf, 2),
                "delay_reduction_s": round(-change, 2),
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

    return {
        "policy": policy.policy,
        "seed": policy.seed,
        "label": "simulated intersection attribution",
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
