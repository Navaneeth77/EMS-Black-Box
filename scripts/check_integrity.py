#!/usr/bin/env python3
"""Part I: the fifteen integrity checks, run over every paired policy run.

    python scripts/check_integrity.py --prefix inc_two_signal

A counterfactual is only a counterfactual if the two runs it compares differ in
exactly one thing. Everything here exists to prove that, and to prove that what
reached the visualisation is what the simulation produced. A check that fails
names the seed and stops it being reported.

Nothing here re-runs a simulation; it reads committed outputs only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "simulation"))

POLICIES = ("NORMAL", "EMS_NEXT", "EMS_ROLLING", "EMS_FULL_PREEMPTION")


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def check_seed(cf_dir: Path, prefix: str, seed: int) -> dict:
    """Run every check for one seed. Returns a per-check PASS/FAIL record."""
    runs: dict[str, dict] = {}
    for policy in POLICIES:
        path = cf_dir / f"{prefix}_seed{seed}_{policy}.json"
        if not path.is_file():
            return {
                "seed": seed,
                "passed": False,
                "checks": [
                    {"check": "runs_exist", "result": "FAIL", "detail": f"missing {path.name}"}
                ],
            }
        runs[policy] = load(path)

    checks: list[dict] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    repro = {p: runs[p]["reproducibility"] for p in POLICIES}
    scen = {p: repro[p]["scenario"] for p in POLICIES}
    base = scen["NORMAL"]

    # 1-8: the paired-scenario identity. Everything but the policy must match.
    record(
        "1_same_network",
        len({repro[p]["network_sha256"] for p in POLICIES}) == 1,
        repro["NORMAL"]["network_sha256"][:16],
    )
    record("2_same_demand", len({s["demand_config_hash"] for s in scen.values()}) == 1)
    record("3_same_seed", all(s["seed"] == seed for s in scen.values()))
    record(
        "4_same_vehicle_mix",
        len({json.dumps(s["vehicle_mix"], sort_keys=True) for s in scen.values()}) == 1,
    )
    record(
        "5_same_ambulance_route",
        len({tuple(s["ambulance_route_edges"]) for s in scen.values()}) == 1,
        f"{len(base['ambulance_route_edges'])} edges",
    )
    record(
        "6_same_ambulance_od",
        len(
            {
                (s["ambulance_origin"], s["ambulance_destination"], s["ambulance_depart_s"])
                for s in scen.values()
            }
        )
        == 1,
    )
    incidents = {p: (runs[p]["run"].get("incident") or {}).get("incident") for p in POLICIES}
    hashes = {json.dumps(i.get("config_hash") if i else None) for i in incidents.values()}
    record("7_same_disturbance", len(hashes) == 1, str(list(hashes)[0]))
    record(
        "8_only_policy_differs",
        len({repro[p]["scenario_hash"] for p in POLICIES}) == 1
        and len({runs[p]["run"]["policy"] for p in POLICIES}) == 4,
        repro["NORMAL"]["scenario_hash"],
    )

    # 9-10: the ambulance was observed, not driven.
    record(
        "9_no_ambulance_teleport",
        not any(runs[p]["run"]["ambulance"]["teleported"] for p in POLICIES),
    )
    # A speed above the vType maximum would mean something wrote to the vehicle.
    max_speeds = {
        p: max(
            (e.get("ambulance_speed_ms", 0.0) or 0.0)
            for e in runs[p]["run"]["policy_report"]["state_transitions"]
        )
        if runs[p]["run"]["policy_report"]["state_transitions"]
        else 0.0
        for p in POLICIES
    }
    record(
        "10_no_artificial_ambulance_speed",
        all(v <= 70 / 3.6 + 0.5 for v in max_speeds.values()),
        f"max observed {max(max_speeds.values()):.2f} m/s vs vType 19.44",
    )

    # 11: nothing is rendered that the simulation did not produce. This is
    # proven by the scene validator, which checks exported scene positions
    # against the FCD recording; it is read here rather than re-derived.
    scene_validation = REPO_ROOT / "data" / "processed" / "silk_board_v1" / "scene_validation.json"
    if scene_validation.is_file():
        report = load(scene_validation)
        record(
            "11_no_frontend_only_traffic",
            bool(report.get("passed")),
            f"{report['totals']['checks']} scene checks, "
            f"{report['totals']['failures']} failures, "
            f"{report['totals']['vehicle_samples_scanned']} samples",
        )
    else:
        record("11_no_frontend_only_traffic", False, "scene_validation.json not found")

    # 12-13: signals.
    record(
        "12_signal_states_from_sumo",
        all(len(runs[p]["run"]["signal_conflicts"]) == 0 for p in POLICIES),
        "0 states outside the programs' own phase sets",
    )
    tls_on_route = {len(runs[p]["run"]["route_traffic_lights"]) for p in POLICIES}
    record("13_expected_tls_present", len(tls_on_route) == 1 and tls_on_route.pop() >= 1)

    # Network teleports are reported per policy, not just totalled. A teleport is
    # SUMO's gridlock escape: that vehicle's travel time is not a time anyone
    # drove, and it sits inside the traffic aggregate. A policy that causes more
    # of them is imposing a cost that the aggregate partly hides, so the delta is
    # surfaced. It is reported rather than failed - teleports are information
    # about the scenario, not proof of a broken run.
    teleports = {p: runs[p]["run"]["simulation"]["teleports"]["count"] for p in POLICIES}
    worst = max(teleports.values())
    checks.append(
        {
            "check": "network_teleports_by_policy",
            "result": "PASS",
            "detail": ", ".join(f"{p}={teleports[p]}" for p in POLICIES)
            + (
                f"  [NOTE: {worst} under one policy vs {teleports['NORMAL']} under NORMAL"
                " — a teleported vehicle's travel time is not a time anyone drove]"
                if worst > teleports["NORMAL"]
                else ""
            ),
        }
    )

    # 14-15: metrics come from the run, and the ambulance completed.
    record(
        "14_ambulance_completed",
        all(runs[p]["run"]["ambulance"]["completed"] for p in POLICIES),
    )
    record(
        "15_metrics_from_simulation",
        all(runs[p]["run"]["ambulance"]["travel_time_s"] is not None for p in POLICIES)
        and all(
            runs[p]["run"]["simulation"]["teleports"]["observations_match_sumo"] for p in POLICIES
        ),
    )

    # 16: the 3D scene must show the same numbers the research recorded.
    # The manifest embeds the paired comparison at export time; this proves the
    # embedded copy still matches its source, so the viewer cannot drift away
    # from the result it illustrates.
    scene_manifest = REPO_ROOT / "frontend" / "public" / "scene" / "manifest.json"
    if scene_manifest.is_file():
        manifest = load(scene_manifest)
        scenario = manifest.get("scenario", {})
        if scenario.get("seed") == seed:
            comparison = manifest.get("comparison") or {}
            policy = comparison.get("policy")
            if policy and policy in POLICIES and not comparison.get("is_baseline"):
                source = cf_dir / f"{prefix}_seed{seed}_comparisons.json"
                agreed = False
                detail = "paired comparison file missing"
                if source.is_file():
                    block = load(source).get("comparisons", {}).get(policy)
                    if block:
                        agreed = (
                            abs(
                                (comparison.get("time_saved_s") or 0)
                                - block["ambulance_time_saved_s"]
                            )
                            < 1e-6
                        )
                        detail = (
                            f"scene {comparison.get('time_saved_s')} s vs "
                            f"research {block['ambulance_time_saved_s']} s"
                        )
                record("16_scene_matches_research", agreed, detail)
            else:
                record(
                    "16_scene_matches_research",
                    True,
                    "scene is the baseline export; no saving to cross-check",
                )

    return {
        "seed": seed,
        "passed": all(c["result"] == "PASS" for c in checks),
        "checks": checks,
        "policies": {
            p: {
                "travel_time_s": runs[p]["run"]["ambulance"]["travel_time_s"],
                "waiting_time_s": runs[p]["run"]["ambulance"]["waiting_time_s"],
                "time_loss_s": runs[p]["run"]["ambulance"]["time_loss_s"],
                "stops": runs[p]["run"]["ambulance"]["stop_count"],
                "route_length_m": runs[p]["run"]["ambulance"].get("route_length_m"),
                "transitions": runs[p]["run"]["policy_report"]["transition_count"],
                "signal_changes": runs[p]["run"]["policy_report"]["signal_change_count"],
                "conflicts": len(runs[p]["run"]["signal_conflicts"]),
                "teleports": runs[p]["run"]["simulation"]["teleports"]["count"],
                "queues": runs[p]["run"].get("queues", {}),
                "incident_applied": (runs[p]["run"].get("incident") or {}).get("applied"),
            }
            for p in POLICIES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--prefix", default="inc_two_signal")
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44, 45, 46])
    args = parser.parse_args()

    cf_dir = REPO_ROOT / "data" / "processed" / args.area / "counterfactual"
    results = [check_seed(cf_dir, args.prefix, seed) for seed in args.seeds]

    print(f"=== INTEGRITY CHECKS — {args.prefix} ===")
    for result in results:
        failed = [c for c in result["checks"] if c["result"] == "FAIL"]
        status = "PASS" if result["passed"] else "FAIL"
        print(f"  seed {result['seed']}: {status} ({len(result['checks'])} checks)")
        for check in failed:
            print(f"     !! {check['check']}: {check['detail']}")

    out = REPO_ROOT / "data" / "processed" / args.area / f"integrity_{args.prefix}.json"
    out.write_text(json.dumps({"prefix": args.prefix, "seeds": results}, indent=2) + "\n")
    passed = all(r["passed"] for r in results)
    print(f"\n  RESULT: {'PASS' if passed else 'FAIL'}")
    print(f"  wrote {out.relative_to(REPO_ROOT)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
