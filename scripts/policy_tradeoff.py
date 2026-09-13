#!/usr/bin/env python3
"""Trade-off and robustness analysis across the five seeds.

    python scripts/policy_tradeoff.py --prefix inc_two_signal

Reads committed run outputs only. Answers the questions a policy ranking has to
answer before it is allowed to name a winner:

* Do the ambulance savings hold across seeds, or are they a property of one?
* Does the apparent ordering EMS_NEXT < EMS_ROLLING < EMS_FULL survive every
  seed, or does it come and go?
* Does stronger preemption buy its extra ambulance seconds with a traffic-side
  cost, and specifically with more teleports?
* Are the same intersections responsible in every seed?

**Policies are deliberately not ranked by ambulance time saved alone.** The
whole point of the counterfactual method is that an intervention has two sides,
and the side that is easy to measure is not automatically the side that matters.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "simulation"))

POLICIES = ("NORMAL", "EMS_NEXT", "EMS_ROLLING", "EMS_FULL_PREEMPTION")
EMS = POLICIES[1:]


def describe(values: list[float]) -> dict:
    if not values:
        return {}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 3),
        "median": round(statistics.median(values), 3),
        "stdev": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--prefix", default="inc_two_signal")
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44, 45, 46])
    args = parser.parse_args()

    cf = REPO_ROOT / "data" / "processed" / args.area / "counterfactual"
    runs: dict[tuple[int, str], dict] = {}
    for seed in args.seeds:
        for policy in POLICIES:
            path = cf / f"{args.prefix}_seed{seed}_{policy}.json"
            if path.is_file():
                runs[(seed, policy)] = json.loads(path.read_text())["run"]

    missing = [f"seed{s}_{p}" for s in args.seeds for p in POLICIES if (s, p) not in runs]

    # ---- paired differences, per seed -----------------------------------
    paired: dict[str, dict[int, dict]] = {p: {} for p in EMS}
    for seed in args.seeds:
        base = runs.get((seed, "NORMAL"))
        if base is None:
            continue
        b_amb = base["ambulance"]
        b_q = base.get("queues", {})
        b_tel = base["simulation"]["teleports"]["count"]
        for policy in EMS:
            run = runs.get((seed, policy))
            if run is None:
                continue
            amb = run["ambulance"]
            q = run.get("queues", {})
            paired[policy][seed] = {
                "travel_saved_s": round(b_amb["travel_time_s"] - amb["travel_time_s"], 3),
                "waiting_saved_s": round(b_amb["waiting_time_s"] - amb["waiting_time_s"], 3),
                "time_loss_reduction_s": round(b_amb["time_loss_s"] - amb["time_loss_s"], 3),
                "stop_reduction": b_amb["stop_count"] - amb["stop_count"],
                "teleports_normal": b_tel,
                "teleports_policy": run["simulation"]["teleports"]["count"],
                "teleport_delta": run["simulation"]["teleports"]["count"] - b_tel,
                "route_max_queue_m": q.get("route_max_queue_length_m"),
                "route_max_queue_delta_m": (
                    round(q["route_max_queue_length_m"] - b_q["route_max_queue_length_m"], 2)
                    if q.get("route_max_queue_length_m") is not None
                    and b_q.get("route_max_queue_length_m") is not None
                    else None
                ),
                "max_halting": q.get("max_halting_vehicles"),
                "recovery_time_s": q.get("recovery_time_s"),
            }

        comp_path = cf / f"{args.prefix}_seed{seed}_comparisons.json"
        if comp_path.is_file():
            blocks = json.loads(comp_path.read_text()).get("comparisons", {})
            for policy in EMS:
                if policy in blocks and seed in paired[policy]:
                    deltas = blocks[policy]["traffic"]["excluding_ambulance"]["deltas"]
                    paired[policy][seed]["traffic_time_loss_delta_s"] = deltas["total_time_loss_s"][
                        "absolute"
                    ]
                    paired[policy][seed]["traffic_vehicles_delta"] = deltas["vehicles_completed"][
                        "absolute"
                    ]

    # ---- ordering robustness --------------------------------------------
    ordering: dict[int, dict] = {}
    for seed in args.seeds:
        saved = {p: paired[p].get(seed, {}).get("travel_saved_s") for p in EMS}
        if any(v is None for v in saved.values()):
            continue
        holds = saved["EMS_NEXT"] <= saved["EMS_ROLLING"] <= saved["EMS_FULL_PREEMPTION"]
        ordering[seed] = {
            "saved": saved,
            "next_le_rolling": saved["EMS_NEXT"] <= saved["EMS_ROLLING"],
            "rolling_le_full": saved["EMS_ROLLING"] <= saved["EMS_FULL_PREEMPTION"],
            "full_ordering_holds": holds,
            "full_minus_rolling_s": round(saved["EMS_FULL_PREEMPTION"] - saved["EMS_ROLLING"], 3),
        }
    holds_count = sum(1 for v in ordering.values() if v["full_ordering_holds"])

    # ---- outliers --------------------------------------------------------
    outliers = []
    for policy in EMS:
        values = {s: v["travel_saved_s"] for s, v in paired[policy].items()}
        if len(values) < 3:
            continue
        mean = statistics.fmean(values.values())
        sd = statistics.stdev(values.values())
        for seed, value in values.items():
            if sd > 0 and abs(value - mean) > 2 * sd:
                outliers.append(
                    {
                        "policy": policy,
                        "seed": seed,
                        "value_s": value,
                        "mean_s": round(mean, 3),
                        "sd_s": round(sd, 3),
                        "z": round((value - mean) / sd, 2),
                    }
                )

    # ---- disturbance consistency ----------------------------------------
    disturbance = {}
    for seed in args.seeds:
        base = runs.get((seed, "NORMAL"))
        if base is None:
            continue
        q = base.get("queues", {})
        incident = (base.get("incident") or {}).get("incident") or {}
        disturbance[seed] = {
            "config_hash": incident.get("config_hash"),
            "applied": (base.get("incident") or {}).get("applied"),
            "route_max_queue_m": q.get("route_max_queue_length_m"),
            "max_halting": q.get("max_halting_vehicles"),
            "peak_halting_during": q.get("peak_halting_during_incident"),
            "recovery_time_s": q.get("recovery_time_s"),
            "baseline_travel_s": base["ambulance"]["travel_time_s"],
            "baseline_waiting_s": base["ambulance"]["waiting_time_s"],
            "baseline_stops": base["ambulance"]["stop_count"],
        }
    hashes = {d["config_hash"] for d in disturbance.values()}

    # ---- intersection consistency ---------------------------------------
    per_tls: dict[str, dict[str, list[float]]] = {}
    for seed in args.seeds:
        comp_path = cf / f"{args.prefix}_seed{seed}_comparisons.json"
        if not comp_path.is_file():
            continue
        blocks = json.loads(comp_path.read_text()).get("intersection_attribution", {})
        for policy, block in blocks.items():
            for entry in block.get("intersections", []):
                slot = per_tls.setdefault(entry["tls_id"], {})
                slot.setdefault(policy, []).append(entry["delay_reduction_s"])

    intersections = []
    for tls, by_policy in per_tls.items():
        all_values = [v for values in by_policy.values() for v in values]
        intersections.append(
            {
                "tls_id": tls,
                "mean_recoverable_s": round(statistics.fmean(all_values), 3),
                "max_recoverable_s": round(max(all_values), 3),
                "observations": len(all_values),
                "seeds_with_nonzero": sum(1 for v in all_values if abs(v) > 0.05),
                "by_policy": {p: describe(v) for p, v in by_policy.items()},
            }
        )
    intersections.sort(key=lambda e: -e["mean_recoverable_s"])

    summary = {
        p: {
            metric: describe([v[metric] for v in paired[p].values() if v.get(metric) is not None])
            for metric in (
                "travel_saved_s",
                "waiting_saved_s",
                "time_loss_reduction_s",
                "stop_reduction",
                "teleport_delta",
                "traffic_time_loss_delta_s",
                "route_max_queue_m",
            )
        }
        for p in EMS
    }

    payload = {
        "prefix": args.prefix,
        "seeds": args.seeds,
        "missing_runs": missing,
        "paired_per_seed": paired,
        "cross_seed": summary,
        "ordering": {
            "per_seed": ordering,
            "holds_in": f"{holds_count}/{len(ordering)}",
            "robust": holds_count == len(ordering) and len(ordering) == len(args.seeds),
        },
        "outliers": outliers,
        "disturbance_consistency": {
            "per_seed": disturbance,
            "single_config_hash": len(hashes) == 1,
            "config_hashes": sorted(h for h in hashes if h),
        },
        "intersections": intersections,
        "data_class": "SIMULATED_DATA",
    }
    out = REPO_ROOT / "data" / "processed" / args.area / f"tradeoff_{args.prefix}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")

    # ---- report ----------------------------------------------------------
    print(f"=== POLICY TRADE-OFF — {args.prefix} ===")
    if missing:
        print(f"  MISSING: {missing}")
    print(f"\n  {'policy':22} {'saved_s':>18} {'traffic_Δ_s':>18} {'teleport_Δ':>16}")
    for p in EMS:
        s_ = summary[p]["travel_saved_s"]
        t_ = summary[p]["traffic_time_loss_delta_s"]
        tel = summary[p]["teleport_delta"]
        print(
            f"  {p:22} {s_.get('mean'):>8} ±{s_.get('stdev'):<8} "
            f"{t_.get('mean'):>10} ±{t_.get('stdev'):<6} "
            f"{tel.get('mean'):>7} ±{tel.get('stdev'):<6}"
        )
    print(f"\n  ordering NEXT<=ROLLING<=FULL holds in {payload['ordering']['holds_in']} seeds")
    for seed, block in ordering.items():
        print(
            f"    seed {seed}: NEXT {block['saved']['EMS_NEXT']:>6} | "
            f"ROLLING {block['saved']['EMS_ROLLING']:>6} | "
            f"FULL {block['saved']['EMS_FULL_PREEMPTION']:>6}  "
            f"(FULL−ROLLING {block['full_minus_rolling_s']:+.1f}s) "
            f"{'OK' if block['full_ordering_holds'] else 'BROKEN'}"
        )
    print(f"\n  outliers (|z|>2): {outliers or 'none'}")
    print(
        f"  disturbance identical across seeds: "
        f"{payload['disturbance_consistency']['single_config_hash']}"
    )
    print("\n  intersections by mean recoverable delay:")
    for entry in intersections:
        print(
            f"    {entry['tls_id'][:34]:36} {entry['mean_recoverable_s']:>7.2f} s "
            f"(nonzero in {entry['seeds_with_nonzero']}/{entry['observations']})"
        )
    print(f"\n  wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
