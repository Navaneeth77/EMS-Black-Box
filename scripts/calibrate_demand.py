#!/usr/bin/env python3
"""Find the largest demand scale this network carries without gridlocking.

    python scripts/calibrate_demand.py --scales 0.4 0.5 0.6 --duration 1500

The base flow rates were set by judgement, and judgement turned out to exceed the
model's capacity: at scale 1.0 the run produced 148 teleports and left 955
vehicles unable to enter. A gridlocked run reports travel times that are not the
times to drive those journeys, so the scale has to be brought down until the
demand meets its own stated basis — congested but moving.

This sweeps candidate scales and reports what each does. It picks nothing: the
choice is recorded in ``ems_sim.demand.config.DEFAULT_DEMAND_SCALE`` and in
``docs/TRAFFIC_DEMAND.md``, so it is visible in a diff rather than buried in a
run that happened once.

Teleports accumulate as a network fills, so a short sweep understates the
gridlock a full run would reach. The chosen scale must be confirmed at full
duration.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.demand.generator import generate_demand  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402
from ems_sim.runner.sumo_process import SumoRunOptions  # noqa: E402
from ems_sim.runner.traci_bridge import (  # noqa: E402
    enrich_from_statistics,
    enrich_from_tripinfo,
    run_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scales", type=float, nargs="+", default=[0.4, 0.5, 0.6])
    parser.add_argument("--duration", type=float, default=1500.0)
    parser.add_argument("--warmup", type=float, default=300.0)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--period", default=str(DemandPeriod.EVENING_PEAK))
    parser.add_argument("--teleport-threshold", type=int, default=10)
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "simulation" / "results" / "calibration.json"
    )
    args = parser.parse_args()

    installation = require_sumo()
    net_file = REPO_ROOT / "simulation" / "sumo" / "silk_board_v1" / "silk_board_v1.net.xml"
    rows = []

    for scale in args.scales:
        config = make_config(
            period=DemandPeriod(args.period),
            seed=args.seed,
            duration_s=args.duration,
            warmup_s=args.warmup,
            demand_scale=scale,
        )
        print(f"\n=== scale {scale:g} — {config.total_vehicles_per_hour:.0f} veh/h ===")
        demand = generate_demand(config, DEFAULT_AMBULANCE_TRIP, net_file, REPO_ROOT, installation)
        output_dir = REPO_ROOT / "simulation" / "results" / f"calib_{config.demand_id}"
        output_dir.mkdir(parents=True, exist_ok=True)

        started = time.monotonic()
        measurements = run_baseline(
            SumoRunOptions(
                net_file=net_file,
                route_files=(demand.routes_file,),
                begin_s=config.begin_s,
                end_s=config.end_s,
                step_length_s=config.step_length_s,
                seed=config.seed,
                tripinfo_output=output_dir / "tripinfo.xml",
                statistic_output=output_dir / "statistics.xml",
            ),
            DEFAULT_AMBULANCE_TRIP.vehicle_id,
            installation,
            sample_interval_s=60.0,
        )
        enrich_from_statistics(measurements, output_dir / "statistics.xml")
        enrich_from_tripinfo(measurements, output_dir / "tripinfo.xml")
        elapsed = time.monotonic() - started

        ambulance = measurements.trips.get(DEFAULT_AMBULANCE_TRIP.vehicle_id)
        backlog_fraction = (
            measurements.insertion_backlog_at_end / measurements.loaded
            if measurements.loaded
            else 0.0
        )
        row = {
            "scale": scale,
            "vehicles_per_hour": config.total_vehicles_per_hour,
            "loaded": measurements.loaded,
            "departed": measurements.departed,
            "arrived": measurements.arrived,
            "insertion_backlog": measurements.insertion_backlog_at_end,
            "insertion_backlog_fraction": round(backlog_fraction, 4),
            "teleports": len(measurements.teleports),
            "sumo_teleports": measurements.sumo_reported_teleports,
            "collisions": measurements.collisions,
            "ambulance_completed": ambulance is not None and ambulance.arrival_s is not None,
            "ambulance_travel_time_s": ambulance.travel_time_s if ambulance else None,
            "wall_clock_s": round(elapsed, 1),
            "within_teleport_threshold": len(measurements.teleports) <= args.teleport_threshold,
        }
        rows.append(row)
        print(
            f"  loaded {row['loaded']:5d}  departed {row['departed']:5d}  "
            f"arrived {row['arrived']:5d}  backlog {row['insertion_backlog']:4d} "
            f"({backlog_fraction:.1%})"
        )
        print(
            f"  TELEPORTS {row['teleports']:4d}  ambulance completed="
            f"{row['ambulance_completed']} "
            f"travel={row['ambulance_travel_time_s']}"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "note": (
                    "Calibration sweep. Teleports accumulate as a network fills, so "
                    "a short sweep understates what a full run reaches; confirm the "
                    "chosen scale at full duration."
                ),
                "duration_s": args.duration,
                "warmup_s": args.warmup,
                "seed": args.seed,
                "teleport_threshold": args.teleport_threshold,
                "results": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {args.out.relative_to(REPO_ROOT)}")

    viable = [r for r in rows if r["within_teleport_threshold"]]
    if viable:
        best = max(viable, key=lambda r: r["scale"])
        print(f"\nLargest scale within the teleport threshold at this duration: {best['scale']:g}")
        print("Confirm it at full duration before adopting it.")
    else:
        print("\nNo tested scale stayed within the teleport threshold. Try lower scales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
