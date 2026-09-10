#!/usr/bin/env python3
"""Phase 3 entry point: generate demand and run the baseline simulation.

    python scripts/run_baseline.py                      # weekday evening peak
    python scripts/run_baseline.py --period off_peak
    python scripts/run_baseline.py --dry-run            # show the plan only
    python scripts/run_baseline.py --demand-only        # generate + validate routes
    python scripts/run_baseline.py --duration 600       # short run

Nothing here is a measurement of Bengaluru traffic. The demand is an assumption
made by this project; the results are simulated. Both are labelled in every file
the run produces.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation", REPO_ROOT / "analysis"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP, AmbulanceTripConfig  # noqa: E402
from ems_sim.demand.baseline import BaselineError, run_phase3_baseline  # noqa: E402
from ems_sim.demand.config import (  # noqa: E402
    BOUNDARY_SINKS,
    BOUNDARY_SOURCES,
    DEFAULT_SEED,
    DemandPeriod,
    make_config,
)
from ems_sim.demand.generator import DemandGenerationError, generate_demand  # noqa: E402
from ems_sim.demand.vehicle_types import VEHICLE_TYPES  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402


def _print_plan(config, ambulance, installation) -> None:
    print("SUMO (resolved, not from PATH)")
    print(f"  SUMO_HOME : {installation.home}")
    print(f"  sumo      : {installation.binary('sumo')}")
    print(f"  duarouter : {installation.binary('duarouter')}")
    print(f"  version   : {installation.version}")
    print(f"\nDemand   : {config.demand_id}")
    print(f"  period      : {config.period}  (scaling applied to base flow rates)")
    print(f"  seed        : {config.seed}")
    print(f"  hash        : {config.config_hash()}")
    print(
        f"  window      : {config.begin_s:.0f}-{config.end_s:.0f} s "
        f"(warm-up {config.warmup_s:.0f} s, step {config.step_length_s} s)"
    )
    print(f"  OD flows    : {len(config.flows)}")
    print(f"  demand scale: {config.demand_scale:g} (calibrated; see TRAFFIC_DEMAND.md)")
    print(f"  total rate  : {config.total_vehicles_per_hour:.0f} veh/h")
    print(f"  vehicle mix : {config.vehicle_mix}")
    print(f"  types       : {[t.type_id for t in VEHICLE_TYPES]}")
    print(f"\nBoundary : {len(BOUNDARY_SOURCES)} sources, {len(BOUNDARY_SINKS)} sinks")
    print(f"\nAmbulance: {ambulance.vehicle_id}")
    print(
        f"  {ambulance.origin_edge} -> {ambulance.destination_edge} "
        f"at t={ambulance.depart_time_s:.0f}s"
    )
    print("  priority : none in this phase (obeys normal traffic rules)")
    print("\nESTIMATED_DATA: demand rates, vehicle parameters, ambulance trip.")
    print("SIMULATED_DATA: everything the run produces.")
    print("Neither is a measurement of Bengaluru traffic.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument(
        "--period",
        default=DemandPeriod.EVENING_PEAK,
        choices=[str(p) for p in DemandPeriod],
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--duration",
        type=float,
        default=3600.0,
        help="Measured duration in seconds, after warm-up.",
    )
    parser.add_argument("--warmup", type=float, default=300.0)
    parser.add_argument("--step-length", type=float, default=0.5)
    parser.add_argument(
        "--demand-scale",
        type=float,
        default=None,
        help="Multiplier on every flow rate. Defaults to the "
        "calibrated value in ems_sim.demand.config.",
    )
    parser.add_argument("--teleport-threshold", type=int, default=10)
    parser.add_argument("--sample-interval", type=float, default=10.0)
    parser.add_argument("--ambulance-from", default=DEFAULT_AMBULANCE_TRIP.origin_edge)
    parser.add_argument("--ambulance-to", default=DEFAULT_AMBULANCE_TRIP.destination_edge)
    parser.add_argument(
        "--ambulance-depart", type=float, default=DEFAULT_AMBULANCE_TRIP.depart_time_s
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--demand-only", action="store_true")
    parser.add_argument("--allow-validation-errors", action="store_true")
    args = parser.parse_args()

    installation = require_sumo()
    from ems_sim.demand.config import DEFAULT_DEMAND_SCALE  # noqa: PLC0415

    config = make_config(
        period=DemandPeriod(args.period),
        seed=args.seed,
        duration_s=args.duration,
        warmup_s=args.warmup,
        step_length_s=args.step_length,
        demand_scale=(args.demand_scale if args.demand_scale is not None else DEFAULT_DEMAND_SCALE),
    )
    ambulance = AmbulanceTripConfig(
        origin_edge=args.ambulance_from,
        destination_edge=args.ambulance_to,
        depart_time_s=args.ambulance_depart,
    )
    _print_plan(config, ambulance, installation)

    if args.dry_run:
        print("\n[dry run] Nothing generated, nothing simulated.")
        return 0

    net_file = REPO_ROOT / "simulation" / "sumo" / args.area / f"{args.area}.net.xml"

    if args.demand_only:
        print("\nGenerating demand…")
        try:
            demand = generate_demand(config, ambulance, net_file, REPO_ROOT, installation)
        except DemandGenerationError as exc:
            print(f"\nDEMAND GENERATION FAILED.\n{exc}", file=sys.stderr)
            return 3
        print(f"  flows  : {demand.flows_file.relative_to(REPO_ROOT)}")
        print(f"  routes : {demand.routes_file.relative_to(REPO_ROOT)}")
        print(f"  vehicles: {demand.vehicle_count} {demand.vehicles_by_type}")
        print(f"  ambulance route: {len(demand.ambulance_route_edges)} edges")
        return 0

    print("\nRunning baseline…")
    started = time.monotonic()
    try:
        result = run_phase3_baseline(
            config,
            ambulance,
            REPO_ROOT,
            area_id=args.area,
            teleport_threshold=args.teleport_threshold,
            allow_validation_errors=args.allow_validation_errors,
            sample_interval_s=args.sample_interval,
        )
    except (BaselineError, DemandGenerationError) as exc:
        print(f"\nBASELINE STOPPED.\n{exc}", file=sys.stderr)
        return 4

    elapsed = time.monotonic() - started
    measurements = result.measurements
    print(
        f"\n  wall clock      : {elapsed:.1f}s "
        f"({measurements.sim_end_time_s / max(elapsed, 1e-9):.1f}x real time)"
    )
    print(f"  steps           : {measurements.steps_executed}")
    print(f"  vehicles loaded : {measurements.loaded}")
    print(f"  departed        : {measurements.departed}")
    print(f"  arrived         : {measurements.arrived}")
    print(f"  running at end  : {measurements.still_running_at_end}")
    print(f"  insertion backlog: {measurements.insertion_backlog_at_end}")
    print(
        f"  TELEPORTS       : {len(measurements.teleports)} "
        f"(SUMO reports {measurements.sumo_reported_teleports})"
    )
    for event in measurements.teleports[:10]:
        print(f"      t={event.sim_time_s:.1f}s {event.vehicle_id} on {event.edge_id}")
    print(f"  collisions      : {measurements.collisions}")

    trip = result.ambulance_trip
    print(f"\n  AMBULANCE {trip['vehicle_id']}")
    print(f"    completed    : {trip.get('completed')}")
    if trip.get("completed"):
        print(f"    travel time  : {trip['travel_time_s']:.1f}s")
        print(f"    waiting time : {trip['waiting_time_s']:.1f}s")
        print(f"    time loss    : {trip['time_loss_s']:.1f}s")
        print(f"    route length : {trip['route_length_m']:.0f}m")
        print(f"    stops        : {trip['stop_count']}")
    else:
        print(f"    {trip.get('note')}")

    print("\n--- validation ---")
    for line in result.report.summary_lines():
        print("  " + line)
    passed = sum(1 for c in result.report.checks if c.passed)
    print(
        f"\n  {len(result.report.errors)} error(s), {len(result.report.warnings)} "
        f"warning(s), {passed}/{len(result.report.checks)} checks passed"
    )

    print("\n--- written ---")
    for path in result.written_files + result.provenance_files:
        print(f"  {path.relative_to(REPO_ROOT)}")
    print(f"  {result.sumocfg_file.relative_to(REPO_ROOT)}")
    print(f"  {result.demand.flows_file.relative_to(REPO_ROOT)}")
    print(f"  {result.demand.routes_file.relative_to(REPO_ROOT)}")
    print(
        f"  {result.output_dir.relative_to(REPO_ROOT)}/ "
        f"(tripinfo, summary, statistics, queues, vehroutes)"
    )

    print(
        "\nDemand is ESTIMATED_DATA; results are SIMULATED_DATA. Neither is a "
        "measurement of\nBengaluru traffic. Counterfactual replay is Phase 5."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
