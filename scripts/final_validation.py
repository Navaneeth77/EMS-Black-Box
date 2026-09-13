#!/usr/bin/env python3
"""Parts J/K/R: metrics, attribution and the final cross-seed validation table.

    python scripts/final_validation.py --prefix inc_two_signal

Reads committed run outputs only; runs nothing. Produces
``docs/archive/FINAL_VALIDATION.md`` and a machine-readable companion.

Every seed and every policy is reported, including the ones whose numbers are
unhelpful. Selecting seeds after seeing their results is the failure mode this
whole project is built to avoid.
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


def write_markdown(payload: dict, path: Path) -> None:
    """The cross-seed table, every run included.

    Every seed and policy appears, including runs whose numbers are unhelpful.
    Reporting only the informative ones is the failure this project exists to
    avoid, and the whole point of fixing the seeds in advance.
    """
    rows = payload["rows"]
    summary = payload["cross_seed"]
    lines: list[str] = []
    add = lines.append

    add("# Final cross-seed validation")
    add("")
    add("> **Simulated results.** Under this simulation scenario only — estimated")
    add("> demand, netconvert-generated signal timings, and a hypothetical incident")
    add("> declared by this project. Not a measurement of real ambulance performance")
    add("> or of real traffic in Bengaluru.")
    add("")
    add(f"Scenario prefix `{payload['prefix']}` · seeds {payload['seeds']} · ")
    add(f"{payload['runs_found']}/{payload['runs_expected']} runs present.")
    if payload["missing"]:
        add("")
        add(f"**MISSING RUNS:** {payload['missing']}")
    add("")
    add("## 1. Every run")
    add("")
    add(
        "| Seed | Policy | Travel (s) | Waiting (s) | Time loss (s) | Stops | "
        "Halts at red | Traffic queue max (m) | EMS transitions | Signal changes | "
        "Conflicts | Teleports | Status |"
    )
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        status = "valid" if r["completed"] and r["conflicts"] == 0 else "CHECK"
        add(
            f"| {r['seed']} | {r['policy']} | {r['travel_time_s']} | {r['waiting_time_s']} | "
            f"{r['time_loss_s']} | {r['stops']} | {r['halts_at_red']} | "
            f"{r['route_max_queue_m']} | {r['transitions']} | {r['signal_changes']} | "
            f"{r['conflicts']} | {r['teleports']} | {status} |"
        )

    add("")
    add("## 2. Cross-seed summary")
    add("")
    nt = summary["normal_travel_time_s"]
    add(
        f"**NORMAL baseline travel time:** mean {nt.get('mean')} s, median "
        f"{nt.get('median')} s, sd {nt.get('stdev')} s, range "
        f"{nt.get('min')}–{nt.get('max')} s (n={nt.get('n')})."
    )
    add("")
    add("| Policy | Mean saved (s) | Median | sd | Min | Max | Mean improvement (%) |")
    add("|---|---|---|---|---|---|---|")
    for policy, block in summary["policies"].items():
        ts = block["time_saved_s"]
        imp = block["improvement_pct"]
        add(
            f"| {policy} | **{ts.get('mean')}** | {ts.get('median')} | {ts.get('stdev')} | "
            f"{ts.get('min')} | {ts.get('max')} | {imp.get('mean')} |"
        )
    add("")
    add("Per-seed saving, so nothing is hidden inside a mean:")
    add("")
    add("| Policy | " + " | ".join(f"seed {s}" for s in payload["seeds"]) + " |")
    add("|---" * (len(payload["seeds"]) + 1) + "|")
    for policy, block in summary["policies"].items():
        cells = [
            str(block["per_seed"].get(str(s), block["per_seed"].get(s, {})).get("saved_s", "—"))
            for s in payload["seeds"]
        ]
        add(f"| {policy} | " + " | ".join(cells) + " |")

    add("")
    add("### Traffic-side term — DIAGNOSTIC ONLY")
    add("")
    add("| Policy | Mean Δ total time loss (s) | sd | Min | Max |")
    add("|---|---|---|---|---|")
    for policy, block in summary["policies"].items():
        td = block["traffic_delta_s"]
        add(
            f"| {policy} | {td.get('mean')} | {td.get('stdev')} | "
            f"{td.get('min')} | {td.get('max')} |"
        )
    add("")
    add("Not a cost or benefit of signal priority. Two controls reproduced changes")
    add("of this size and sign with **no ambulance in the network**, and across")
    add("seeds the term has no stable sign. See `FINAL_RND_REPORT.md` section 18.")

    add("")
    add("## 3. Intersection attribution — ranked by recoverable delay")
    add("")
    add("| Traffic light | Mean recoverable delay (s) | Max | Observations |")
    add("|---|---|---|---|")
    for entry in payload["intersection_ranking"]:
        add(
            f"| `{entry['tls_id'][:46]}` | **{entry['mean_recoverable_delay_s']}** | "
            f"{entry['max_recoverable_delay_s']} | {entry['observations']} |"
        )
    add("")
    add("Delay is attributed from the ambulance's own recorded traversal of each")
    add("signal's approach, comparing the paired policy run against NORMAL. A")
    add("signal is never charged delay for being near the route.")
    add("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--prefix", default="inc_two_signal")
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44, 45, 46])
    args = parser.parse_args()

    cf = REPO_ROOT / "data" / "processed" / args.area / "counterfactual"
    rows: list[dict] = []
    comparisons: dict[str, dict[int, dict]] = {p: {} for p in EMS}
    attribution: list[dict] = []
    missing: list[str] = []

    for seed in args.seeds:
        for policy in POLICIES:
            path = cf / f"{args.prefix}_seed{seed}_{policy}.json"
            if not path.is_file():
                missing.append(path.name)
                continue
            run = json.loads(path.read_text())["run"]
            amb = run["ambulance"]
            queues = run.get("queues", {})
            incident = (run.get("incident") or {}).get("incident")
            rows.append(
                {
                    "seed": seed,
                    "policy": policy,
                    "travel_time_s": amb["travel_time_s"],
                    "waiting_time_s": amb["waiting_time_s"],
                    "time_loss_s": amb["time_loss_s"],
                    "stops": amb["stop_count"],
                    "distance_m": amb.get("route_length_m"),
                    "completed": amb["completed"],
                    "signal_wait_events": len(amb.get("signal_wait_events") or []),
                    "halts_at_red": sum(
                        1
                        for e in (amb.get("signal_wait_events") or [])
                        if e.get("stopped_by_signal")
                    ),
                    "transitions": run["policy_report"]["transition_count"],
                    "signal_changes": run["policy_report"]["signal_change_count"],
                    "conflicts": len(run["signal_conflicts"]),
                    "teleports": run["simulation"]["teleports"]["count"],
                    "max_halting": queues.get("max_halting_vehicles"),
                    "route_max_queue_m": queues.get("route_max_queue_length_m"),
                    "network_max_queue_m": queues.get("network_max_queue_length_m"),
                    "recovery_time_s": queues.get("recovery_time_s"),
                    "incident": incident["config_hash"] if incident else None,
                }
            )

        comp_path = cf / f"{args.prefix}_seed{seed}_comparisons.json"
        if comp_path.is_file():
            payload = json.loads(comp_path.read_text())
            for policy, block in payload.get("comparisons", {}).items():
                comparisons[policy][seed] = {
                    "saved_s": block["ambulance_time_saved_s"],
                    "improvement_pct": block["ambulance_improvement_percent"],
                    "traffic_delta_s": block["traffic"]["excluding_ambulance"]["deltas"][
                        "total_time_loss_s"
                    ]["absolute"],
                }
            for policy, block in payload.get("intersection_attribution", {}).items():
                for entry in block.get("intersections", []):
                    attribution.append(
                        {
                            "seed": seed,
                            "policy": policy,
                            "tls_id": entry["tls_id"],
                            "green_fraction": entry["green_fraction_for_ambulance"],
                            "baseline_traversal_s": entry["baseline_traversal_time_s"],
                            "ems_traversal_s": entry["counterfactual_traversal_time_s"],
                            "recoverable_delay_s": entry["delay_reduction_s"],
                            "transitions_here": entry.get("state_transitions_here"),
                        }
                    )

    # ---- cross-seed summaries -------------------------------------------
    normal_travel = [r["travel_time_s"] for r in rows if r["policy"] == "NORMAL"]
    summary = {
        "normal_travel_time_s": describe(normal_travel),
        "normal_waiting_time_s": describe(
            [r["waiting_time_s"] for r in rows if r["policy"] == "NORMAL"]
        ),
        "normal_time_loss_s": describe([r["time_loss_s"] for r in rows if r["policy"] == "NORMAL"]),
        "policies": {
            p: {
                "time_saved_s": describe([v["saved_s"] for v in comparisons[p].values()]),
                "improvement_pct": describe(
                    [v["improvement_pct"] for v in comparisons[p].values()]
                ),
                "traffic_delta_s": describe(
                    [v["traffic_delta_s"] for v in comparisons[p].values()]
                ),
                "per_seed": comparisons[p],
            }
            for p in EMS
        },
    }

    # ---- intersection ranking by recoverable delay ----------------------
    by_tls: dict[str, list[float]] = {}
    for entry in attribution:
        by_tls.setdefault(entry["tls_id"], []).append(entry["recoverable_delay_s"])
    ranking = sorted(
        (
            {
                "tls_id": tls,
                "mean_recoverable_delay_s": round(statistics.fmean(v), 3),
                "max_recoverable_delay_s": round(max(v), 3),
                "observations": len(v),
            }
            for tls, v in by_tls.items()
        ),
        key=lambda r: -r["mean_recoverable_delay_s"],
    )

    payload = {
        "prefix": args.prefix,
        "seeds": args.seeds,
        "runs_expected": len(args.seeds) * len(POLICIES),
        "runs_found": len(rows),
        "missing": missing,
        "rows": rows,
        "cross_seed": summary,
        "intersection_ranking": ranking,
        "attribution": attribution,
        "data_class": "SIMULATED_DATA",
        "interpretation": (
            "Simulated results under estimated demand and netconvert-generated "
            "signal timings. Not a measurement of real ambulance performance or "
            "real traffic in Bengaluru."
        ),
    }
    out = REPO_ROOT / "data" / "processed" / args.area / f"final_validation_{args.prefix}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"=== FINAL VALIDATION — {args.prefix} ===")
    print(
        f"  runs {len(rows)}/{payload['runs_expected']}"
        + (f"  MISSING {missing}" if missing else "")
    )
    print(f"  NORMAL travel: {summary['normal_travel_time_s']}")
    for p in EMS:
        print(f"  {p:22} saved {summary['policies'][p]['time_saved_s']}")
    print("  intersection ranking (mean recoverable delay):")
    for entry in ranking:
        print(
            f"    {entry['tls_id'][:34]:36} "
            f"{entry['mean_recoverable_delay_s']:>7.2f} s  (n={entry['observations']})"
        )
    write_markdown(payload, REPO_ROOT / "docs" / "FINAL_VALIDATION.md")
    print(f"  wrote {out.relative_to(REPO_ROOT)} and docs/archive/FINAL_VALIDATION.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
