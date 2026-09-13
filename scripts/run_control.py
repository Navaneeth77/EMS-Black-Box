#!/usr/bin/env python3
"""Phase 5a control: does the signal perturbation alone move network traffic?

    python scripts/run_control.py --seed 42

Phase 5a's Candidate D run showed every EMS policy *reducing* network-wide time
loss by 25,000-37,000 s. A priority policy acting for one vehicle in ~2,900
should not improve traffic; that number needs a control before it is believed.

The control isolates the two candidate causes:

  A. the ambulance and its interaction with traffic, or
  B. the signal perturbation itself, applied to netconvert's unoptimised
     fixed-time plans.

**Method.** Each EMS policy is run once with the ambulance, recording the state
of every watched traffic light at every step. Those recorded state timelines are
then replayed, step for step, into a run with **no ambulance in the demand**. The
signal perturbation is therefore reproduced exactly while the ambulance's
influence on traffic is removed.

**The approximations, stated exactly.** They are not zero:

1. The control demand is the Candidate D demand *minus one vehicle* — the
   ambulance. Every other vehicle, its type, route and departure time is
   unchanged. One vehicle in 2,898 is removed, and the paired NORMAL control
   measures what that removal alone does.
2. Replay writes signal states directly instead of selecting phases the way a
   policy does. Every replayed state is validated against its program's phase
   set, so no state occurs that the program could not have produced, but the
   mechanism differs from the live policy.
3. SUMO is a deterministic but chaotic microsimulation. Removing one vehicle
   perturbs the arrival order downstream, so control-NORMAL is not expected to
   equal the with-ambulance NORMAL exactly. The size of that difference is
   itself a measurement, and it is reported.

Nothing here is tuned. No policy parameter, demand setting, network, signal
program or vehicle behaviour is changed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation", REPO_ROOT / "analysis"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.calibration.scenario import DEFAULT_VARIANT, ensure_variant_network  # noqa: E402
from ems_sim.counterfactual.runner import route_tls_for  # noqa: E402
from ems_sim.demand.ambulance import AMBULANCE_TRIPS  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.demand.generator import generate_demand  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.policies.base import AmbulanceObservation  # noqa: E402
from ems_sim.policies.policies import make_policy  # noqa: E402
from ems_sim.provenance import (  # noqa: E402
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.sumo_env import require_sumo, sumo_tools_on_path  # noqa: E402
from ems_sim.runner.sumo_process import (  # noqa: E402
    SumoRunOptions,
    build_sumo_command,
    free_port,
    sumo_environment,
)

EMS_POLICIES = ("EMS_NEXT", "EMS_ROLLING", "EMS_FULL_PREEMPTION")


def _traci(installation):
    tools = sumo_tools_on_path(installation)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import traci

    return traci


def strip_ambulance(routes_file: Path, ambulance_id: str, output: Path) -> int:
    """Write a copy of the demand with the ambulance removed. Nothing else changes."""
    tree = ET.parse(routes_file)
    root = tree.getroot()
    removed = 0
    for parent in [root]:
        for vehicle in list(parent):
            if vehicle.get("id") == ambulance_id:
                parent.remove(vehicle)
                removed += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return removed


def queue_stats(queue_file: Path) -> dict[str, float | None]:
    """Max and mean queue length over every lane and timestep SUMO reported."""
    if not queue_file.is_file():
        return {"max_queue_length_m": None, "mean_queue_length_m": None, "samples": 0}
    total = 0.0
    count = 0
    peak = 0.0
    for _event, element in ET.iterparse(queue_file, events=("end",)):
        if element.tag == "lane":
            length = float(element.get("queueing_length", 0.0))
            total += length
            count += 1
            peak = max(peak, length)
            element.clear()
    return {
        "max_queue_length_m": round(peak, 2),
        "mean_queue_length_m": round(total / count, 4) if count else None,
        "samples": count,
    }


def trip_stats(tripinfo: Path, exclude_id: str | None) -> dict[str, float | int]:
    """Aggregate completed trips, optionally excluding the ambulance."""
    travel = waiting = loss = 0.0
    count = 0
    for _event, element in ET.iterparse(tripinfo, events=("end",)):
        if element.tag != "tripinfo":
            continue
        if exclude_id is None or element.get("id") != exclude_id:
            travel += float(element.get("duration", 0.0))
            waiting += float(element.get("waitingTime", 0.0))
            loss += float(element.get("timeLoss", 0.0))
            count += 1
        element.clear()
    return {
        "vehicles_completed": count,
        "total_travel_time_s": round(travel, 2),
        "total_waiting_time_s": round(waiting, 2),
        "total_time_loss_s": round(loss, 2),
        "mean_travel_time_s": round(travel / count, 3) if count else None,
        "mean_waiting_time_s": round(waiting / count, 3) if count else None,
        "mean_time_loss_s": round(loss / count, 3) if count else None,
    }


def run(
    options: SumoRunOptions,
    installation,
    net_file: Path,
    route_edges: list[str],
    *,
    policy_name: str | None = None,
    ambulance_id: str | None = None,
    replay: dict[str, list[str]] | None = None,
    record: bool = False,
) -> dict:
    """One SUMO run.

    Three modes, one loop, so the three share every measurement path:
      * ``policy_name`` set + ``record``  -> live policy with the ambulance, logging states
      * ``replay`` set                    -> forced signal states, no policy, no ambulance
      * neither                           -> plain run
    """
    traci = _traci(installation)
    route_tls = route_tls_for(net_file, route_edges)
    watched = [entry.tls_id for entry in route_tls]
    allowed = {entry.tls_id: set(entry.program.phase_states) for entry in route_tls}

    policy = make_policy(policy_name) if policy_name else None
    route_positions = {edge: index for index, edge in enumerate(route_edges)}
    recorded: dict[str, list[str]] = {tls_id: [] for tls_id in watched} if record else {}
    conflicts: list[dict] = []
    replay_mismatch: list[dict] = []
    teleports: list[dict] = []
    transitions = signal_changes = 0
    steps = 0
    started = time.monotonic()

    port = free_port()
    command = build_sumo_command(options, traci_port=None, installation=installation)
    import os

    os.environ.update(sumo_environment(installation))
    traci.start(command, port=port)
    try:
        if policy:
            policy.on_simulation_start(route_tls, traci)
        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            steps += 1
            now = traci.simulation.getTime()
            if now >= options.end_s:
                break

            for vehicle_id in traci.simulation.getStartingTeleportIDList():
                teleports.append({"vehicle_id": vehicle_id, "sim_time_s": now})

            if replay is not None:
                index = steps - 1
                for tls_id in watched:
                    timeline = replay.get(tls_id, [])
                    if index < len(timeline):
                        state = timeline[index]
                        if state not in allowed[tls_id]:
                            replay_mismatch.append({"tls_id": tls_id, "sim_time_s": now})
                            continue
                        traci.trafficlight.setRedYellowGreenState(tls_id, state)

            if policy:
                observation = AmbulanceObservation(
                    present=ambulance_id in traci.vehicle.getIDList()
                )
                if observation.present:
                    with contextlib.suppress(traci.TraCIException):
                        observation.edge_id = traci.vehicle.getRoadID(ambulance_id)
                        observation.lane_id = traci.vehicle.getLaneID(ambulance_id)
                        observation.lane_position_m = traci.vehicle.getLanePosition(ambulance_id)
                        observation.speed_ms = traci.vehicle.getSpeed(ambulance_id)
                        observation.route_index = route_positions.get(observation.edge_id)
                        for entry in route_tls:
                            observation.distance_to_tls_m[entry.tls_id] = (
                                traci.vehicle.getDrivingDistance(
                                    ambulance_id, entry.approach_edge, 0.0
                                )
                            )
                policy.on_step(now, observation, traci)

            for tls_id in watched:
                state = traci.trafficlight.getRedYellowGreenState(tls_id)
                if record:
                    recorded[tls_id].append(state)
                if state not in allowed[tls_id]:
                    conflicts.append({"tls_id": tls_id, "sim_time_s": now, "state": state})

        if policy:
            report = policy.on_simulation_end()
            transitions = report.get("transition_count", 0)
            signal_changes = report.get("signal_change_count", 0)
        sim_end = traci.simulation.getTime()
    finally:
        with contextlib.suppress(Exception):
            traci.close()

    result = {
        "steps_executed": steps,
        "sim_end_time_s": sim_end,
        "wall_clock_s": round(time.monotonic() - started, 1),
        "signal_transitions": transitions,
        "signal_changes": signal_changes,
        "signal_conflicts": conflicts,
        "replay_state_rejected": replay_mismatch,
        "teleports": teleports,
        "teleport_count": len(teleports),
    }
    return result, recorded


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--trip", default="two_signal", choices=sorted(AMBULANCE_TRIPS))
    parser.add_argument("--duration", type=float, default=3600.0)
    parser.add_argument("--warmup", type=float, default=300.0)
    args = parser.parse_args()

    installation = require_sumo()
    study_area = get_study_area(args.area)
    variant = DEFAULT_VARIANT
    net_file = ensure_variant_network(variant, study_area, REPO_ROOT, installation)
    ambulance = AMBULANCE_TRIPS[args.trip]

    from dataclasses import replace

    config = make_config(
        period=DemandPeriod.EVENING_PEAK,
        seed=args.seed,
        duration_s=args.duration,
        warmup_s=args.warmup,
    )
    config = replace(config, demand_id=f"cf_{args.area}_{args.trip}_seed{args.seed}")

    print(f"Phase 5a control — {args.area}, trip {args.trip}, seed {args.seed}")
    demand = generate_demand(config, ambulance, net_file, REPO_ROOT, installation)
    route = demand.ambulance_route_edges
    print(
        f"  ambulance route: {len(route)} edges "
        f"({ambulance.origin_edge} -> {ambulance.destination_edge})"
    )

    control_routes = (
        REPO_ROOT / "simulation" / "routes" / f"{config.demand_id}_no_ambulance.rou.xml"
    )
    removed = strip_ambulance(demand.routes_file, ambulance.vehicle_id, control_routes)
    if removed != 1:
        raise SystemExit(f"expected to remove exactly 1 ambulance, removed {removed}")
    print(f"  control demand: {control_routes.name} (ambulance removed, {removed} vehicle)")

    def options_for(tag: str, routes: Path) -> SumoRunOptions:
        out = REPO_ROOT / "simulation" / "results" / f"{config.demand_id}_control_{tag}"
        out.mkdir(parents=True, exist_ok=True)
        return SumoRunOptions(
            net_file=net_file,
            route_files=(routes,),
            begin_s=config.begin_s,
            end_s=config.end_s,
            step_length_s=config.step_length_s,
            seed=args.seed,
            tripinfo_output=out / "tripinfo.xml",
            statistic_output=out / "statistics.xml",
            queue_output=out / "queues.xml",
        )

    runs: dict[str, dict] = {}

    # --- 1. record the signal timeline each policy produces, ambulance present
    #
    # NORMAL is recorded and replayed too. Replaying a timeline is not perfectly
    # neutral — forcing a state each step takes the signal off program control —
    # so if only the EMS controls were replayed, the replay mechanism itself
    # would be confounded with the perturbation under test. Every control run
    # therefore goes through the identical replay path, and the only thing that
    # differs between them is the content of the recorded timeline.
    timelines: dict[str, dict[str, list[str]]] = {}
    for name in ("NORMAL", *EMS_POLICIES):
        print(f"\n=== recording {name} (ambulance present) ===", flush=True)
        options = options_for(f"record_{name}", demand.routes_file)
        result, recorded = run(
            options,
            installation,
            net_file,
            route,
            policy_name=None if name == "NORMAL" else name,
            ambulance_id=ambulance.vehicle_id,
            record=True,
        )
        timelines[name] = recorded
        result["traffic_excluding_ambulance"] = trip_stats(
            options.tripinfo_output, ambulance.vehicle_id
        )
        result["queues"] = queue_stats(options.queue_output)
        runs[f"record_{name}"] = result
        print(
            f"  {result['signal_transitions']} transitions, {result['signal_changes']} changes, "
            f"{len(result['signal_conflicts'])} conflicts, {result['teleport_count']} teleports"
        )

    # How much does each recorded timeline actually differ from NORMAL's? If a
    # policy's timeline were identical to NORMAL's, its control would be a
    # re-run of the NORMAL control and could not test anything.
    timeline_divergence = {}
    for name in EMS_POLICIES:
        per_tls = {}
        for tls_id, states in timelines[name].items():
            baseline = timelines["NORMAL"].get(tls_id, [])
            pairs = list(zip(states, baseline, strict=False))
            differing = sum(1 for a, b in pairs if a != b)
            per_tls[tls_id] = {
                "steps_compared": len(pairs),
                "steps_differing": differing,
                "percent_differing": round(differing / len(pairs) * 100, 3) if pairs else None,
            }
        timeline_divergence[name] = per_tls
    print("\n=== recorded timeline divergence from NORMAL ===")
    for name, per_tls in timeline_divergence.items():
        for tls_id, stats in per_tls.items():
            print(
                f"  {name:22} {tls_id[:34]:36} "
                f"{stats['steps_differing']:>6} / {stats['steps_compared']} steps differ "
                f"({stats['percent_differing']}%)"
            )

    # --- 2. the controls: no ambulance in the demand
    print("\n=== control NORMAL (no ambulance, replayed NORMAL timeline) ===", flush=True)
    options = options_for("NORMAL", control_routes)
    result, _ = run(options, installation, net_file, route, replay=timelines["NORMAL"])
    result["traffic_excluding_ambulance"] = trip_stats(options.tripinfo_output, None)
    result["queues"] = queue_stats(options.queue_output)
    runs["control_NORMAL"] = result
    print(
        f"  {result['traffic_excluding_ambulance']['vehicles_completed']} completed, "
        f"{result['teleport_count']} teleports"
    )

    for name in EMS_POLICIES:
        print(f"\n=== control {name} (no ambulance, replayed signal timeline) ===", flush=True)
        options = options_for(name, control_routes)
        result, _ = run(options, installation, net_file, route, replay=timelines[name])
        result["traffic_excluding_ambulance"] = trip_stats(options.tripinfo_output, None)
        result["queues"] = queue_stats(options.queue_output)
        runs[f"control_{name}"] = result
        print(
            f"  {result['traffic_excluding_ambulance']['vehicles_completed']} completed, "
            f"{len(result['replay_state_rejected'])} rejected states, "
            f"{result['teleport_count']} teleports"
        )

    # --- 3. paired differences against the control NORMAL --------------------
    base = runs["control_NORMAL"]["traffic_excluding_ambulance"]
    comparisons = {}
    for name in EMS_POLICIES:
        policy_traffic = runs[f"control_{name}"]["traffic_excluding_ambulance"]
        comparisons[name] = {
            key: {
                "control_normal": base[key],
                "control_policy": policy_traffic[key],
                "absolute": round(policy_traffic[key] - base[key], 3),
                "percent": (
                    round((policy_traffic[key] - base[key]) / base[key] * 100, 3)
                    if base[key]
                    else None
                ),
            }
            for key in (
                "vehicles_completed",
                "total_travel_time_s",
                "total_waiting_time_s",
                "total_time_loss_s",
                "mean_time_loss_s",
            )
        }

    out_dir = REPO_ROOT / "data" / "processed" / args.area / "control"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "reproducibility": {
            "generated_at": utc_now_iso(),
            "sumo_version": installation.version,
            "network_sha256": sha256_file(net_file),
            "demand_config": config.as_dict(),
            "ambulance_trip": ambulance.as_dict(),
            "scenario_variant": variant.as_dict(),
            "control_routes_file": str(control_routes.relative_to(REPO_ROOT)),
        },
        "method": {
            "question": (
                "Does the network-wide traffic improvement seen under the EMS policies "
                "survive when the ambulance is removed and only the signal perturbation "
                "remains?"
            ),
            "design": (
                "Each policy's watched-signal state timeline is recorded with the "
                "ambulance present, then replayed step-for-step into a run whose demand "
                "is identical except that the ambulance is removed."
            ),
            "approximations": [
                "Control demand is the Candidate D demand minus exactly one vehicle "
                "(the ambulance). Nothing else differs.",
                "Replay writes signal states directly rather than selecting phases; "
                "every replayed state is validated against the program's phase set.",
                "SUMO is deterministic but chaotic: removing one vehicle perturbs "
                "downstream arrival order, so control NORMAL is not expected to equal "
                "the with-ambulance NORMAL exactly. That difference is reported.",
            ],
        },
        "recorded_timeline_divergence_from_normal": timeline_divergence,
        "runs": runs,
        "paired_vs_control_normal": comparisons,
        "data_class": str(DataClass.SIMULATED),
    }
    target = out_dir / f"{args.trip}_seed{args.seed}_control.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    write_record(
        ProvenanceRecord(
            dataset_id=f"phase5a_control_{args.trip}_seed{args.seed}",
            data_class=DataClass.SIMULATED,
            description=(
                "Phase 5a control: the EMS signal perturbations replayed into an "
                "ambulance-free network, to test whether the traffic-side improvement "
                "is caused by the ambulance or by the signal perturbation itself."
            ),
            produced_by=f"scripts/run_control.py --seed {args.seed} --trip {args.trip}",
            random_seed=args.seed,
            source_name="EMS Black Box Phase 5a",
            retrieved_at=utc_now_iso(),
            file_path=str(target.relative_to(REPO_ROOT)),
            derived_from=[f"phase5_counterfactual_{args.trip}_seed{args.seed}"],
            processing_steps=[
                "Recorded each policy's watched-signal state timeline with the ambulance present.",
                "Removed exactly one vehicle (the ambulance) from the demand.",
                "Replayed each timeline step-for-step into the ambulance-free run.",
                "Validated every replayed state against the program's phase set.",
                "Paired every control against the ambulance-free NORMAL control.",
            ],
            tool_versions={"sumo": installation.version or "unknown"},
            limitations=[
                "SIMULATED RESULT. Not a measurement of real traffic in Bengaluru.",
                "Replay reproduces the signal timeline, not the mechanism that produced it.",
                "One seed. Seeds 43-46 have not been run.",
                "Signal programs are netconvert-generated ESTIMATED_DATA; the effect "
                "under test may be a property of that unoptimised timing.",
            ],
        ),
        REPO_ROOT / "data" / "provenance",
    )

    print("\n=== control, paired against control NORMAL (no ambulance anywhere) ===")
    print(
        f"{'policy':22} {'veh':>6} {'totLoss':>12} {'d totLoss':>12} "
        f"{'meanLoss':>9} {'d mean':>8} {'maxQ':>8}"
    )
    b = runs["control_NORMAL"]
    print(
        f"{'control_NORMAL':22} {base['vehicles_completed']:>6} {base['total_time_loss_s']:>12.1f} "
        f"{'—':>12} {base['mean_time_loss_s']:>9.2f} {'—':>8} "
        f"{b['queues']['max_queue_length_m']:>8.1f}"
    )
    for name in EMS_POLICIES:
        t = runs[f"control_{name}"]["traffic_excluding_ambulance"]
        q = runs[f"control_{name}"]["queues"]
        c = comparisons[name]
        print(
            f"{'control_' + name:22} {t['vehicles_completed']:>6} {t['total_time_loss_s']:>12.1f} "
            f"{c['total_time_loss_s']['absolute']:>+12.1f} {t['mean_time_loss_s']:>9.2f} "
            f"{c['mean_time_loss_s']['absolute']:>+8.2f} {q['max_queue_length_m']:>8.1f}"
        )

    print(f"\nwrote {target.relative_to(REPO_ROOT)}")
    print("\nSimulated results, one seed. Seeds 43-46 have NOT been run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
