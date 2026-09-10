"""Aggregate statistics across seeded runs.

Two rules shape this module.

**Individual runs are never hidden.** Every aggregate is accompanied by the
per-seed values it came from. A standard deviation with the underlying numbers
discarded cannot be checked, and a reader cannot tell a genuine spread from one
outlier.

**Spread is reported, not smoothed.** The reason for running five seeds is to
find out how much the answer moves when nothing but the random stream changes.
Reporting only the mean would throw away the finding.

The statistics are ordinary and deliberately so: mean, median, standard
deviation, min, max, and percentiles. With five samples a percentile is a crude
estimate, and ``sample_count`` travels with every summary so nobody reads more
precision into it than five runs support.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from ems_sim.calibration.multi_seed import SeedRunResult


@dataclass(frozen=True)
class Distribution:
    """Summary of one measured quantity across seeds, with the samples kept."""

    name: str
    unit: str
    values: tuple[float, ...]
    seeds: tuple[int, ...]

    @property
    def sample_count(self) -> int:
        return len(self.values)

    def as_dict(self) -> dict[str, Any]:
        if not self.values:
            return {
                "name": self.name,
                "unit": self.unit,
                "sample_count": 0,
                "note": "No run produced this measurement. No value has been substituted.",
                "per_seed": {},
            }
        ordered = sorted(self.values)
        payload: dict[str, Any] = {
            "name": self.name,
            "unit": self.unit,
            "sample_count": len(ordered),
            "mean": round(statistics.fmean(ordered), 4),
            "median": round(statistics.median(ordered), 4),
            "min": round(ordered[0], 4),
            "max": round(ordered[-1], 4),
            "range": round(ordered[-1] - ordered[0], 4),
            # Population sd is meaningless for one sample; sample sd needs two.
            "stdev": (round(statistics.stdev(ordered), 4) if len(ordered) > 1 else None),
            "coefficient_of_variation": None,
            # Individual runs, never discarded.
            "per_seed": dict(zip(self.seeds, self.values, strict=True)),
        }
        if payload["mean"]:
            payload["coefficient_of_variation"] = (
                round(payload["stdev"] / payload["mean"], 4)
                if payload["stdev"] is not None
                else None
            )
        if len(ordered) >= 3:
            payload["percentiles"] = {
                "p25": round(_percentile(ordered, 25), 4),
                "p50": round(_percentile(ordered, 50), 4),
                "p75": round(_percentile(ordered, 75), 4),
                "p90": round(_percentile(ordered, 90), 4),
            }
            payload["percentile_note"] = (
                f"Estimated from {len(ordered)} samples by linear interpolation. "
                "With this few runs a percentile is indicative, not precise."
            )
        return payload


def _percentile(ordered: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(runs: list[SeedRunResult], attribute: str, name: str, unit: str) -> Distribution:
    pairs = [(r.seed, getattr(r, attribute)) for r in runs if getattr(r, attribute) is not None]
    return Distribution(
        name=name,
        unit=unit,
        values=tuple(float(v) for _, v in pairs),
        seeds=tuple(s for s, _ in pairs),
    )


def aggregate_runs(runs: list[SeedRunResult]) -> dict[str, Any]:
    """Summarise a set of seeded runs."""
    if not runs:
        return {"error": "No runs to aggregate."}

    ordered = sorted(runs, key=lambda r: r.seed)
    seeds = [r.seed for r in ordered]

    distributions = {
        "vehicles_departed": _distribution(ordered, "departed", "vehicles departed", "count"),
        "vehicles_arrived": _distribution(ordered, "arrived", "vehicles arrived", "count"),
        "vehicles_remaining": _distribution(
            ordered, "remaining", "vehicles still running at end", "count"
        ),
        "insertion_backlog": _distribution(ordered, "backlog", "insertion backlog", "count"),
        "teleports": _distribution(ordered, "teleports", "teleports", "count"),
        "mean_travel_time": _distribution(
            ordered, "mean_travel_time_s", "mean travel time (all vehicles)", "s"
        ),
        "mean_waiting_time": _distribution(
            ordered, "mean_waiting_time_s", "mean waiting time (all vehicles)", "s"
        ),
        "mean_time_loss": _distribution(
            ordered, "mean_time_loss_s", "mean time loss (all vehicles)", "s"
        ),
        "ambulance_travel_time": _distribution(
            ordered, "ambulance_travel_time_s", "ambulance travel time", "s"
        ),
        "ambulance_waiting_time": _distribution(
            ordered, "ambulance_waiting_time_s", "ambulance waiting time", "s"
        ),
        "ambulance_time_loss": _distribution(
            ordered, "ambulance_time_loss_s", "ambulance time loss", "s"
        ),
    }

    # Route consistency across seeds. If the ambulance takes a different path in
    # different runs, its travel times are not samples of the same journey and
    # cannot be pooled — which would matter a great deal to Phase 5.
    routes = {tuple(r.ambulance_route_edges) for r in ordered if r.ambulance_route_edges}
    route_consistent = len(routes) <= 1

    return {
        "seeds": seeds,
        "run_count": len(ordered),
        "demand_config_hashes": sorted({r.demand_config_hash for r in ordered}),
        "variant_ids": sorted({r.variant_id for r in ordered}),
        "distributions": {k: v.as_dict() for k, v in distributions.items()},
        "ambulance_route_consistency": {
            "consistent_across_seeds": route_consistent,
            "distinct_routes": len(routes),
            "edge_count": len(next(iter(routes))) if routes else 0,
            "note": (
                "Every seed produced the same ambulance route, so the travel times "
                "are samples of one journey under different traffic draws."
                if route_consistent
                else "The ambulance took different routes in different seeds. Travel "
                "times are then not samples of the same journey and must not be "
                "pooled — a difference between runs could be a different path "
                "rather than different traffic."
            ),
        },
        "completed_runs": sum(1 for r in ordered if r.ambulance_completed),
        "per_seed": [r.as_dict() for r in ordered],
        "data_class": "SIMULATED_DATA",
        "interpretation": (
            "These distributions describe how much this simulation's output moves "
            "when only the random seed changes. They are a measure of the model's "
            "internal variability, NOT of agreement with observed Bengaluru "
            "traffic. The demand, vehicle behaviour and signal programs are the "
            "same estimated assumptions in every run."
        ),
    }


def aggregate_bottlenecks(runs: list[SeedRunResult], top_n: int = 20) -> dict[str, Any]:
    """Find the locations that are congested consistently, not just once.

    An edge that tops the delay ranking in one seed may be an artefact of that
    draw. One that appears in every seed is a property of the scenario, and
    ``seeds_present`` is what separates the two.

    These are **simulation bottlenecks**. Whether they correspond to real
    congestion at Silk Board is not established by anything here.
    """
    delay: dict[str, dict[str, Any]] = {}
    waiting: dict[str, dict[str, Any]] = {}
    junctions: dict[str, dict[str, Any]] = {}

    for run in runs:
        for record in run.top_delay_edges:
            entry = delay.setdefault(
                record["edge_id"],
                {"edge_id": record["edge_id"], "seeds_present": 0, "time_loss_s": []},
            )
            entry["seeds_present"] += 1
            entry["time_loss_s"].append(record.get("time_loss_s") or 0.0)
        for record in run.top_waiting_edges:
            entry = waiting.setdefault(
                record["edge_id"],
                {"edge_id": record["edge_id"], "seeds_present": 0, "waiting_time_s": []},
            )
            entry["seeds_present"] += 1
            entry["waiting_time_s"].append(record.get("waiting_time_s") or 0.0)
        for record in run.intersection_approach_delay:
            entry = junctions.setdefault(
                record["junction_id"],
                {
                    "junction_id": record["junction_id"],
                    "is_traffic_light": record.get("is_traffic_light", False),
                    "road_names": record.get("road_names", []),
                    "seeds_present": 0,
                    "total_time_loss_s": [],
                },
            )
            entry["seeds_present"] += 1
            entry["total_time_loss_s"].append(record.get("total_time_loss_s") or 0.0)

    def summarise(entries: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
        rows = []
        for entry in entries.values():
            samples = entry.pop(key)
            entry[f"mean_{key}"] = round(statistics.fmean(samples), 2)
            entry[f"min_{key}"] = round(min(samples), 2)
            entry[f"max_{key}"] = round(max(samples), 2)
            entry[f"per_seed_{key}"] = [round(s, 2) for s in samples]
            rows.append(entry)
        return sorted(rows, key=lambda r: (-r["seeds_present"], -r[f"mean_{key}"]))[:top_n]

    return {
        "label": "simulation bottlenecks",
        "definition": (
            "Locations where this simulation accumulated the most time loss or "
            "waiting time. 'seeds_present' counts how many seeded runs ranked the "
            "location in their top set: a location present in every run is a "
            "property of the scenario, one present in a single run may be an "
            "artefact of that draw."
        ),
        "not_a_claim_about_reality": (
            "These are bottlenecks in a simulation built on estimated demand, "
            "estimated vehicle behaviour and netconvert-generated signal programs. "
            "Nothing here establishes that they correspond to congestion at the "
            "real junction."
        ),
        "run_count": len(runs),
        "most_delayed_edges": summarise(delay, "time_loss_s"),
        "highest_waiting_edges": summarise(waiting, "waiting_time_s"),
        "intersection_approaches": summarise(junctions, "total_time_loss_s"),
    }
