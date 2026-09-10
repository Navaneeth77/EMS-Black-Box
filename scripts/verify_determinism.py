#!/usr/bin/env python3
"""Phase 4: verify that the same seed reproduces the same run.

    python scripts/verify_determinism.py --seed 42 --duration 900

Runs one configuration twice and compares the generated demand byte-for-byte and
the simulation results within tolerance.

This is the assumption Phase 5 rests on: a counterfactual attributes the
difference between two runs to the signal policy, and that only holds if
everything else about them is identical.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

from ems_sim.calibration.determinism import (  # noqa: E402
    compare_runs,
    file_sha256,
    route_content_sha256,
)
from ems_sim.calibration.multi_seed import run_seed  # noqa: E402
from ems_sim.calibration.scenario import DEFAULT_VARIANT  # noqa: E402
from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.provenance import utc_now_iso  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=float, default=900.0)
    parser.add_argument("--warmup", type=float, default=300.0)
    args = parser.parse_args()

    installation = require_sumo()
    study_area = get_study_area(args.area)
    config = make_config(
        period=DemandPeriod.EVENING_PEAK,
        seed=args.seed,
        duration_s=args.duration,
        warmup_s=args.warmup,
    )

    print(f"Determinism check — seed {args.seed}, {config.end_s:.0f}s window")
    results = []
    hashes = []
    for attempt in ("A", "B"):
        print(f"\n--- run {attempt} ---", flush=True)
        result = run_seed(
            args.seed,
            config,
            DEFAULT_AMBULANCE_TRIP,
            REPO_ROOT,
            study_area,
            variant=DEFAULT_VARIANT,
            area_id=args.area,
            installation=installation,
            top_n=5,
        )
        results.append(result)
        flows = REPO_ROOT / "simulation" / "demand" / f"{result.demand_id}.flows.xml"
        routes = REPO_ROOT / "simulation" / "routes" / f"{result.demand_id}.rou.xml"
        # The flows file is written by this project and is compared byte-for-byte.
        # The routes file is duarouter's, and carries a generation timestamp, so
        # its ROUTES are compared rather than its bytes.
        hashes.append((file_sha256(flows), route_content_sha256(routes)))
        print(
            f"  departed={result.departed} arrived={result.arrived} "
            f"ambulance={result.ambulance_travel_time_s}s",
            flush=True,
        )

    check = compare_runs(results[0], results[1])
    check.flows_hash_a, check.routes_hash_a = hashes[0]
    check.flows_hash_b, check.routes_hash_b = hashes[1]
    check.demand_identical = hashes[0] == hashes[1]
    check.simulation_equivalent = not check.differences

    print("\n=== result ===")
    print(f"  demand identical      : {check.demand_identical}")
    print(f"    flows  (bytes)   {check.flows_hash_a[:24]}… / {check.flows_hash_b[:24]}…")
    print(f"    routes (content) {check.routes_hash_a[:24]}… / {check.routes_hash_b[:24]}…")
    print(f"  simulation equivalent : {check.simulation_equivalent}")
    for difference in check.differences:
        print(f"    DIFFERS: {difference}")
    print(f"\n  PASSED: {check.passed}")

    out = REPO_ROOT / "data" / "processed" / args.area / "calibration" / "determinism.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "generated_at": utc_now_iso(),
                "sumo_version": installation.version,
                "duration_s": args.duration,
                "check": check.as_dict(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {out.relative_to(REPO_ROOT)}")
    return 0 if check.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
