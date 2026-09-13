#!/usr/bin/env python3
"""The skeptical reviewer: try to disprove the result before believing it.

    python scripts/adversarial_review.py --prefix inc_two_signal

The question this asks is not "do the checks pass" — they did once already while
the disturbance was silently inert. It asks: **could this result have been
produced by a bug, a scenario-selection artifact, inconsistent paired
conditions, or a hidden parameter change?**

Each probe below is an attempt to *break* the finding. A probe that fails to
break it is evidence; a probe that succeeds stops the result being reported.
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
AMBULANCE_MAX_SPEED_MS = 70 / 3.6


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--prefix", default="inc_two_signal")
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44, 45, 46])
    args = parser.parse_args()

    cf = REPO_ROOT / "data" / "processed" / args.area / "counterfactual"
    runs = {}
    for seed in args.seeds:
        for policy in POLICIES:
            path = cf / f"{args.prefix}_seed{seed}_{policy}.json"
            if path.is_file():
                runs[(seed, policy)] = json.loads(path.read_text())

    probes: list[dict] = []

    def probe(name: str, broken: bool, detail: str, question: str) -> None:
        probes.append(
            {
                "probe": name,
                "question": question,
                "result": "BROKEN" if broken else "SURVIVED",
                "detail": detail,
            }
        )

    # 1. Could the saving be a different scenario rather than a different policy?
    mismatches = []
    for seed in args.seeds:
        hashes = {
            runs[(seed, p)]["reproducibility"]["scenario_hash"]
            for p in POLICIES
            if (seed, p) in runs
        }
        if len(hashes) > 1:
            mismatches.append(seed)
    probe(
        "paired_scenario_identity",
        bool(mismatches),
        f"scenario hash identical within every seed; mismatched seeds: {mismatches or 'none'}",
        "Could the four runs of a seed actually be four different scenarios?",
    )

    # 2. Could the route have changed, making this a shorter trip rather than a faster one?
    route_changes = []
    for seed in args.seeds:
        routes = {
            tuple(runs[(seed, p)]["run"]["ambulance"]["route_edges"])
            for p in POLICIES
            if (seed, p) in runs
        }
        if len(routes) > 1:
            route_changes.append(seed)
    lengths = {runs[k]["run"]["ambulance"].get("route_length_m") for k in runs if k[1] == "NORMAL"}
    probe(
        "route_identity",
        bool(route_changes),
        f"route identical across policies in every seed; "
        f"NORMAL route lengths {sorted(x for x in lengths if x)}",
        "Did the ambulance simply take a shorter path under the EMS policies?",
    )

    # 3. Could the ambulance have been given speed?
    speeding = []
    for key, payload in runs.items():
        for transition in payload["run"]["policy_report"]["state_transitions"]:
            speed = transition.get("ambulance_speed_ms") or 0.0
            if speed > AMBULANCE_MAX_SPEED_MS + 0.5:
                speeding.append((key, round(speed, 2)))
    probe(
        "no_artificial_speed",
        bool(speeding),
        f"max recorded ambulance speed within vType 19.44 m/s; violations: {speeding or 'none'}",
        "Was the ambulance made faster rather than the signal made green?",
    )

    # 4. Could the ambulance have teleported past the queue?
    amb_teleports = [k for k, v in runs.items() if v["run"]["ambulance"]["teleported"]]
    probe(
        "no_ambulance_teleport",
        bool(amb_teleports),
        f"ambulance teleported in: {amb_teleports or 'no run'}",
        "Did the ambulance skip the congestion via SUMO's gridlock escape?",
    )

    # 5. Could the disturbance have differed between the paired runs?
    disturbance_hashes = set()
    unapplied = []
    for key, payload in runs.items():
        incident = payload["run"].get("incident") or {}
        config = incident.get("incident") or {}
        disturbance_hashes.add(config.get("config_hash"))
        if not incident.get("applied"):
            unapplied.append(key)
    probe(
        "disturbance_identity",
        len(disturbance_hashes) > 1 or bool(unapplied),
        f"config hashes {sorted(h for h in disturbance_hashes if h)}; "
        f"unapplied in {unapplied or 'no run'}",
        "Did some runs get a different disturbance, or none at all?",
    )

    # 6. Is the disturbance actually doing anything? The defect that hid once.
    #    A disturbance that changes nothing network-wide is an inert instrument,
    #    and its null result is not a finding.
    inert = []
    for seed in args.seeds:
        with_inc = runs.get((seed, "NORMAL"))
        plain = cf / f"two_signal_seed{seed}_NORMAL.json"
        if with_inc is None or not plain.is_file():
            continue
        without = json.loads(plain.read_text())["run"]
        same_arrived = (
            with_inc["run"]["simulation"]["vehicles"]["arrived"]
            == without["simulation"]["vehicles"]["arrived"]
        )
        same_travel = (
            with_inc["run"]["ambulance"]["travel_time_s"] == without["ambulance"]["travel_time_s"]
        )
        if same_arrived and same_travel:
            inert.append(seed)
    probe(
        "disturbance_is_not_inert",
        bool(inert),
        f"seeds where the incident changed neither arrivals nor ambulance "
        f"travel: {inert or 'none'}",
        "Is the disturbance actually perturbing the simulation, or silently doing nothing?",
    )

    # 7. Could the ordering be one seed carrying the rest?
    saved = {p: {} for p in EMS}
    for seed in args.seeds:
        base = runs.get((seed, "NORMAL"))
        if base is None:
            continue
        for policy in EMS:
            run = runs.get((seed, policy))
            if run:
                saved[policy][seed] = (
                    base["run"]["ambulance"]["travel_time_s"]
                    - run["run"]["ambulance"]["travel_time_s"]
                )
    leave_one_out_breaks = []
    common = sorted(set.intersection(*(set(saved[p]) for p in EMS)) if all(saved.values()) else [])
    for dropped in common:
        subset = [s for s in common if s != dropped]
        if not subset:
            continue
        means = {p: statistics.fmean(saved[p][s] for s in subset) for p in EMS}
        if not (means["EMS_NEXT"] <= means["EMS_ROLLING"] <= means["EMS_FULL_PREEMPTION"]):
            leave_one_out_breaks.append(dropped)
    probe(
        "ordering_not_driven_by_one_seed",
        bool(leave_one_out_breaks),
        f"leave-one-out over {common}: ordering breaks when dropping "
        f"{leave_one_out_breaks or 'no seed'}",
        "Does the policy ordering depend on a single seed?",
    )

    # 8. Could the frontend be showing something the simulation did not produce?
    scene_validation = REPO_ROOT / "data" / "processed" / args.area / "scene_validation.json"
    if scene_validation.is_file():
        report = json.loads(scene_validation.read_text())
        probe(
            "no_frontend_only_effects",
            not report.get("passed"),
            f"{report['totals']['checks']} scene checks, {report['totals']['failures']} failures, "
            f"{report['totals']['vehicle_samples_scanned']} vehicle samples",
            "Is the 3D view showing positions or states SUMO never produced?",
        )

    # 9. Could a signal have been driven outside its own program?
    conflicts = sum(len(v["run"]["signal_conflicts"]) for v in runs.values())
    probe(
        "signals_within_program",
        conflicts > 0,
        f"{conflicts} signal states outside their programs' phase sets across {len(runs)} runs",
        "Did a policy write a signal state the controller could not have produced?",
    )

    survived = all(p["result"] == "SURVIVED" for p in probes)
    payload = {
        "prefix": args.prefix,
        "seeds": args.seeds,
        "runs_examined": len(runs),
        "probes": probes,
        "verdict": "SURVIVED" if survived else "BROKEN",
        "data_class": "SIMULATED_DATA",
    }
    out = REPO_ROOT / "data" / "processed" / args.area / f"adversarial_{args.prefix}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"=== ADVERSARIAL REVIEW — {args.prefix} ({len(runs)} runs) ===")
    for entry in probes:
        mark = "✓" if entry["result"] == "SURVIVED" else "✗"
        print(f"  {mark} {entry['probe']:34} {entry['result']}")
        print(f"      Q: {entry['question']}")
        print(f"      {entry['detail']}")
    print(f"\n  VERDICT: {payload['verdict']}")
    print(f"  wrote {out.relative_to(REPO_ROOT)}")
    return 0 if survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
