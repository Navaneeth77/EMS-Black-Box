#!/usr/bin/env python3
"""Choose the HISTORICAL_DEMO demand scale and departure, from NORMAL runs only.

    python scripts/historical_scenario_sweep.py

The demo has to show an ambulance **caught in a queue**, because an ambulance that
arrives first at a red loses nothing to the queue and priority then only saves the
wait for the next green. Two things decide whether that happens: how much traffic
the network is given, and when the ambulance sets off relative to the four-way's
450 s cycle. Both are searched here, on grids fixed in advance, and both are
judged on NORMAL runs. No EMS policy is run and no travel time is compared, so the
scenario cannot have been tuned to flatter priority.

The scale is searched from the **top** of the grid down, because every value is a
fraction of the observed peak hour and a larger one is closer to what was counted.
The departure is searched from the earliest up. For each pair:

* generate the demand (observed peak hour x k, observed car share) and run NORMAL,
  stopping at the ambulance's arrival exactly as the demo does;
* **valid** when there were no teleports, the ambulance arrived, no signal state
  outside the programs was observed, and the insertion backlog is at most 5% of
  the vehicles that entered;
* **usable** when it is also true that the ambulance's first halt within
  ``QUEUE_HALT_WINDOW_M`` of the four-way stop line has at least
  ``MIN_VEHICLES_AHEAD`` vehicles between it and that stop line *and* happens at
  least ``MIN_QUEUE_HALT_DISTANCE_M`` back from it. Both together are what "caught
  in the queue" means: vehicles in front, and not already at its head.

The first usable pair in that order wins: the most traffic the model carries
validly, and the earliest departure at which the ambulance is caught in the queue.
Writes ``data/processed/historical_demo/demand_scale_sweep.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "simulation") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "simulation"))

from ems_sim.counterfactual.runner import run_policy  # noqa: E402
from ems_sim.historical.demand import (  # noqa: E402
    DEPART_GRID_S,
    MAX_BACKLOG_FRACTION,
    MIN_QUEUE_HALT_DISTANCE_M,
    MIN_VEHICLES_AHEAD,
    QUEUE_HALT_WINDOW_M,
    SCALE_GRID,
    TIME_TO_TELEPORT_S,
    generate_historical_demand,
)
from ems_sim.historical.network import TLS_ID, demo_net_file  # noqa: E402
from ems_sim.historical.policy import make_historical_policy  # noqa: E402
from ems_sim.historical.sources import load_observations  # noqa: E402
from ems_sim.historical.trip import SEED, trip_config  # noqa: E402
from ems_sim.provenance import sha256_file, utc_now_iso  # noqa: E402
from ems_sim.runner.sumo_env import require_sumo, sumo_tools_on_path  # noqa: E402
from ems_sim.runner.sumo_process import SumoRunOptions  # noqa: E402

OUT_DIR = REPO_ROOT / "data" / "processed" / "historical_demo"

RULE = (
    "Fixed before the sweep and evaluated on NORMAL runs only. For each k on the scale "
    "grid from the top down (a larger k is a larger share of the observed peak hour), and "
    "each departure on the departure grid from the earliest up: run NORMAL, stopping at "
    "the ambulance's arrival. Valid when 0 teleports, the ambulance arrived, 0 signal "
    f"states outside the programs, and insertion backlog <= {MAX_BACKLOG_FRACTION:.0%} of "
    "departed vehicles. Usable when, in addition, the ambulance's first halt within "
    f"{QUEUE_HALT_WINDOW_M:.0f} m of the four-way stop line has at least "
    f"{MIN_VEHICLES_AHEAD} vehicles between it and that stop line and is at least "
    f"{MIN_QUEUE_HALT_DISTANCE_M:.0f} m back from it. Take the first usable pair. No EMS "
    "policy is run and no travel time is compared."
)


def queue_at_first_halt(result) -> dict | None:
    """The ambulance's first halt in the four-way's queue, as the run recorded it."""
    for event in result.ambulance_signal_waits:
        queue = event.get("queue_ahead")
        distance = (event.get("distance_to_tls_m") or {}).get(TLS_ID)
        if queue is None or distance is None or distance > QUEUE_HALT_WINDOW_M:
            continue
        return {
            "sim_time_s": event["sim_time_s"],
            "edge_id": event["edge_id"],
            "distance_to_four_way_stop_line_m": distance,
            "vehicles_ahead": queue["count"],
            "vehicles_ahead_halted": queue["halted"],
            "stopped_by_signal": event["stopped_by_signal"],
            "classification": event["classification"],
        }
    return None


def main() -> int:
    installation = require_sumo()
    tools = sumo_tools_on_path(installation)
    if tools not in sys.path:
        sys.path.insert(0, tools)

    obs = load_observations(REPO_ROOT)
    selection = json.loads((OUT_DIR / "trip_selection.json").read_text())
    net_file = demo_net_file(REPO_ROOT)

    rows: list[dict] = []
    chosen: dict | None = None
    for k in sorted(SCALE_GRID, reverse=True):
        for depart in DEPART_GRID_S:
            ambulance = trip_config(
                selection["chosen"]["origin"], selection["chosen"]["destination"], depart
            )
            demand = generate_historical_demand(
                obs, k, ambulance, net_file, REPO_ROOT, seed=SEED, installation=installation
            )
            config = demand.config
            run_dir = REPO_ROOT / "simulation" / "results" / f"{config.demand_id}_sweep_NORMAL"
            run_dir.mkdir(parents=True, exist_ok=True)
            options = SumoRunOptions(
                net_file=net_file,
                route_files=(demand.routes_file,),
                begin_s=config.begin_s,
                end_s=config.end_s,
                step_length_s=config.step_length_s,
                seed=SEED,
                time_to_teleport_s=TIME_TO_TELEPORT_S,
                statistic_output=run_dir / "statistics.xml",
            )
            print(f"k={k:g} depart={depart:.0f}s: {demand.vehicle_count} routed ...", flush=True)
            result = run_policy(
                options,
                make_historical_policy("NORMAL"),
                ambulance.vehicle_id,
                net_file,
                demand.ambulance_route_edges,
                installation,
                incident=None,
                stop_on_ambulance_arrival=True,
                measure_distance_to_stop_line=True,
                record_queue_ahead=True,
            )
            measurements = result.measurements
            departed = measurements.departed
            backlog = measurements.insertion_backlog_at_end
            halt = queue_at_first_halt(result)
            checks = {
                "no_teleports": len(measurements.teleports) == 0,
                "ambulance_arrived": result.ambulance_arrived_at_s is not None,
                "no_signal_conflicts": not result.signal_conflicts,
                "backlog_within_bound": backlog <= MAX_BACKLOG_FRACTION * max(departed, 1),
            }
            valid = all(checks.values())
            ahead = halt["vehicles_ahead"] if halt else 0
            halt_distance = halt["distance_to_four_way_stop_line_m"] if halt else 0.0
            caught_in_queue = (
                ahead >= MIN_VEHICLES_AHEAD and halt_distance >= MIN_QUEUE_HALT_DISTANCE_M
            )
            row = {
                "k": k,
                "depart_s": depart,
                "demand_id": config.demand_id,
                "config_hash": config.config_hash(),
                "sumo_input_vph": round(sum(f.vehicles_per_hour for f in config.scaled_flows()), 3),
                "routed_vehicles": demand.vehicle_count,
                "departed": departed,
                "teleports": len(measurements.teleports),
                "insertion_backlog_at_end": backlog,
                "signal_conflicts": len(result.signal_conflicts),
                "sim_end_time_s": measurements.sim_end_time_s,
                "first_queue_halt": halt,
                "vehicles_ahead_at_first_halt": ahead,
                "halt_distance_to_stop_line_m": halt_distance,
                "caught_in_queue": caught_in_queue,
                "checks": checks,
                "valid": valid,
                "usable": valid and caught_in_queue,
                "wall_clock_s": round(measurements.wall_clock_s, 1),
            }
            rows.append(row)
            print(
                f"  valid={valid} vehicles_ahead={ahead} halt_distance={halt_distance:.0f} m "
                f"teleports={row['teleports']} backlog={backlog}/{departed}",
                flush=True,
            )
            if row["usable"]:
                chosen = row
                break
        if chosen:
            break

    record = {
        "mode": "HISTORICAL_DEMO",
        "generated_at": utc_now_iso(),
        "rule": RULE,
        "grid": list(SCALE_GRID),
        "depart_grid_s": list(DEPART_GRID_S),
        "min_vehicles_ahead": MIN_VEHICLES_AHEAD,
        "min_queue_halt_distance_m": MIN_QUEUE_HALT_DISTANCE_M,
        "queue_halt_window_m": QUEUE_HALT_WINDOW_M,
        "label": "ESTIMATED",
        "network_sha256": sha256_file(net_file),
        "trip": {
            "origin": selection["chosen"]["origin"],
            "destination": selection["chosen"]["destination"],
        },
        "seed": SEED,
        "time_to_teleport_s": TIME_TO_TELEPORT_S,
        "ems_policies_run": False,
        "travel_times_recorded": False,
        "rows": rows,
        "selected_k": chosen["k"] if chosen else None,
        "selected_depart_s": chosen["depart_s"] if chosen else None,
        "selected_demand_id": chosen["demand_id"] if chosen else None,
        "selected_vehicles_ahead": chosen["vehicles_ahead_at_first_halt"] if chosen else None,
        "selected_halt_distance_m": chosen["halt_distance_to_stop_line_m"] if chosen else None,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "demand_scale_sweep.json").write_text(json.dumps(record, indent=2) + "\n")
    if chosen is None:
        print("No pair on the grids puts the ambulance in a queue. Stopping.")
        return 1
    print(
        f"selected k = {chosen['k']:g}, depart = {chosen['depart_s']:.0f} s, "
        f"{chosen['vehicles_ahead_at_first_halt']} vehicles ahead, first halt "
        f"{chosen['halt_distance_to_stop_line_m']:.0f} m back from the stop line"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
