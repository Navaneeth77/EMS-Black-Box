#!/usr/bin/env python3
"""Phase 5b control: signal perturbation on a fixed timetable, no ambulance.

    python scripts/run_fixed_schedule_control.py --seed 42

Phase 5a asked whether the EMS policies' traffic-side effect came from the
ambulance or from the perturbation, and answered "the perturbation" — but it had
to replay recorded signal states to do so, and replay is not neutral: forcing a
state every step took the signals off program control and moved total time loss
by ~9,925 s on its own, comparable to the effect being measured.

This control removes that confound entirely. Both arms are ordinary runs with no
override anywhere:

  * ``fixed_NORMAL``     - ambulance-free demand, signals on their own programs.
  * ``fixed_<policy>``   - ambulance-free demand, priority granted at the same
                           traffic lights over the same intervals the recorded
                           EMS policy used, decided from the clock. Priority is
                           granted by selecting among the program's own phases,
                           exactly as the EMS policies do.

The timetable is transcribed from the recorded run's own state transitions. It
is not tuned, and nothing in either arm reads the ambulance.

**Remaining approximation, stated rather than glossed.** The control demand is
the Candidate D demand minus exactly one vehicle. Removing that vehicle is
itself a perturbation, and its size is measured here rather than assumed: the
paired NORMAL arm is ambulance-free too, so it cancels between arms, but the
control is not on the same absolute footing as the with-ambulance experiment.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation", REPO_ROOT / "analysis"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.calibration.scenario import DEFAULT_VARIANT, ensure_variant_network  # noqa: E402
from ems_sim.counterfactual.runner import run_policy  # noqa: E402
from ems_sim.demand.ambulance import AMBULANCE_TRIPS  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.demand.generator import generate_demand  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.policies.policies import make_policy  # noqa: E402
from ems_sim.policies.scheduled import (  # noqa: E402
    ScheduledPerturbationPolicy,
    envelope_from_transitions,
)
from ems_sim.provenance import (  # noqa: E402
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402
from ems_sim.runner.sumo_process import SumoRunOptions  # noqa: E402
from ems_sim.runner.traci_bridge import enrich_from_statistics, enrich_from_tripinfo  # noqa: E402

EMS_POLICIES = ("EMS_NEXT", "EMS_ROLLING", "EMS_FULL_PREEMPTION")
SOURCE_MIN_GREEN = {"EMS_NEXT": 5.0, "EMS_ROLLING": 5.0, "EMS_FULL_PREEMPTION": 3.0}
SOURCE_MAX_PRIORITY = {"EMS_NEXT": 60.0, "EMS_ROLLING": 60.0, "EMS_FULL_PREEMPTION": 600.0}


def strip_ambulance(routes_file: Path, ambulance_id: str, output: Path) -> int:
    tree = ET.parse(routes_file)
    root = tree.getroot()
    removed = 0
    for vehicle in list(root):
        if vehicle.get("id") == ambulance_id:
            root.remove(vehicle)
            removed += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return removed


def queue_stats(queue_file: Path) -> dict[str, float | int | None]:
    if not queue_file.is_file():
        return {"max_queue_length_m": None, "mean_queue_length_m": None, "samples": 0}
    total = peak = 0.0
    count = 0
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


def trip_stats(tripinfo: Path) -> dict[str, float | int | None]:
    travel = waiting = loss = 0.0
    count = 0
    for _event, element in ET.iterparse(tripinfo, events=("end",)):
        if element.tag == "tripinfo":
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

    print(f"Fixed-schedule control — {args.area}, trip {args.trip}, seed {args.seed}")
    demand = generate_demand(config, ambulance, net_file, REPO_ROOT, installation)
    route = demand.ambulance_route_edges

    control_routes = (
        REPO_ROOT / "simulation" / "routes" / f"{config.demand_id}_no_ambulance.rou.xml"
    )
    removed = strip_ambulance(demand.routes_file, ambulance.vehicle_id, control_routes)
    if removed != 1:
        raise SystemExit(f"expected to remove exactly 1 ambulance, removed {removed}")

    # Intervention envelopes, taken from the recorded EMS runs' own transitions.
    cf_dir = REPO_ROOT / "data" / "processed" / args.area / "counterfactual"
    envelopes = {}
    for name in EMS_POLICIES:
        source = cf_dir / f"{args.trip}_seed{args.seed}_{name}.json"
        if not source.is_file():
            raise SystemExit(
                f"missing recorded run {source}. Run scripts/run_counterfactual.py "
                f"--trip {args.trip} --seed {args.seed} first."
            )
        payload = json.loads(source.read_text())["run"]
        envelopes[name] = envelope_from_transitions(
            payload["policy_report"]["state_transitions"], config.end_s
        )
        total = sum(end - start for windows in envelopes[name].values() for start, end in windows)
        print(
            f"  {name}: {sum(len(w) for w in envelopes[name].values())} windows, {total:.1f} s held"
        )

    def options_for(tag: str) -> SumoRunOptions:
        out = REPO_ROOT / "simulation" / "results" / f"{config.demand_id}_fixed_{tag}"
        out.mkdir(parents=True, exist_ok=True)
        return SumoRunOptions(
            net_file=net_file,
            route_files=(control_routes,),
            begin_s=config.begin_s,
            end_s=config.end_s,
            step_length_s=config.step_length_s,
            seed=args.seed,
            tripinfo_output=out / "tripinfo.xml",
            statistic_output=out / "statistics.xml",
            queue_output=out / "queues.xml",
        )

    runs = {}
    arms = [("NORMAL", make_policy("NORMAL"))] + [
        (
            name,
            ScheduledPerturbationPolicy(
                schedule=envelopes[name],
                source_policy=name,
                min_green_s=SOURCE_MIN_GREEN[name],
                max_priority_s=SOURCE_MAX_PRIORITY[name],
            ),
        )
        for name in EMS_POLICIES
    ]

    for tag, policy in arms:
        print(f"\n=== fixed_{tag} (no ambulance, no replay override) ===", flush=True)
        options = options_for(tag)
        result = run_policy(options, policy, ambulance.vehicle_id, net_file, route, installation)
        enrich_from_statistics(result.measurements, options.statistic_output)
        enrich_from_tripinfo(result.measurements, options.tripinfo_output)
        entry = {
            "arm": f"fixed_{tag}",
            "policy": policy.name,
            "source_policy": tag,
            "signal_transitions": result.policy_report["transition_count"],
            "signal_changes": result.policy_report["signal_change_count"],
            "signal_conflicts": [c.as_dict() for c in result.signal_conflicts],
            "teleport_count": len(result.measurements.teleports),
            "teleports": [t.vehicle_id for t in result.measurements.teleports],
            "ambulance_present": result.ambulance is not None,
            "traffic": trip_stats(options.tripinfo_output),
            "queues": queue_stats(options.queue_output),
            "sim_end_time_s": result.measurements.sim_end_time_s,
            "steps_executed": result.measurements.steps_executed,
        }
        runs[f"fixed_{tag}"] = entry
        print(
            f"  {entry['signal_transitions']} transitions, {entry['signal_changes']} changes, "
            f"{len(entry['signal_conflicts'])} conflicts, {entry['teleport_count']} teleports, "
            f"{entry['traffic']['vehicles_completed']} completed"
        )

    base = runs["fixed_NORMAL"]["traffic"]
    comparisons = {}
    for name in EMS_POLICIES:
        arm = runs[f"fixed_{name}"]["traffic"]
        comparisons[name] = {
            key: {
                "fixed_normal": base[key],
                "fixed_policy": arm[key],
                "absolute": round(arm[key] - base[key], 3),
                "percent": (
                    round((arm[key] - base[key]) / base[key] * 100, 3) if base[key] else None
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
    target = out_dir / f"{args.trip}_seed{args.seed}_fixed_schedule_control.json"
    target.write_text(
        json.dumps(
            {
                "reproducibility": {
                    "generated_at": utc_now_iso(),
                    "sumo_version": installation.version,
                    "network_sha256": sha256_file(net_file),
                    "demand_config": config.as_dict(),
                    "ambulance_trip_removed": ambulance.as_dict(),
                    "scenario_variant": variant.as_dict(),
                    "control_routes_file": str(control_routes.relative_to(REPO_ROOT)),
                },
                "method": {
                    "question": (
                        "Does a signal perturbation with no ambulance logic reproduce the "
                        "traffic-side effect attributed to the EMS policies?"
                    ),
                    "design": (
                        "Both arms are ambulance-free ordinary runs with no state override. "
                        "The intervention arm grants priority by selecting among the "
                        "program's own phases, on a timetable transcribed from the recorded "
                        "EMS run's state transitions."
                    ),
                    "improvement_over_phase5a": (
                        "Phase 5a's control replayed recorded signal states, which moved "
                        "total time loss by ~9,925 s on its own. No replay is used here, so "
                        "that footprint is absent from both arms."
                    ),
                    "remaining_approximation": (
                        "The control demand is the Candidate D demand minus exactly one "
                        "vehicle (the ambulance). Both arms share that removal, so it "
                        "cancels between them, but the control is not on the same absolute "
                        "footing as the with-ambulance experiment."
                    ),
                },
                "intervention_envelopes": {
                    name: {tls: [[a, b] for a, b in w] for tls, w in envelopes[name].items()}
                    for name in EMS_POLICIES
                },
                "runs": runs,
                "paired_vs_fixed_normal": comparisons,
                "data_class": str(DataClass.SIMULATED),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    write_record(
        ProvenanceRecord(
            dataset_id=f"phase5b_fixed_schedule_control_{args.trip}_seed{args.seed}",
            data_class=DataClass.SIMULATED,
            description=(
                "Phase 5b control: the EMS intervention envelopes applied on a fixed "
                "timetable in an ambulance-free network, with no state replay, to test "
                "whether generic signal perturbation reproduces the traffic-side effect."
            ),
            produced_by=(
                f"scripts/run_fixed_schedule_control.py --seed {args.seed} --trip {args.trip}"
            ),
            random_seed=args.seed,
            source_name="EMS Black Box Phase 5b",
            retrieved_at=utc_now_iso(),
            file_path=str(target.relative_to(REPO_ROOT)),
            derived_from=[f"phase5_counterfactual_{args.trip}_seed{args.seed}"],
            processing_steps=[
                "Recovered each EMS policy's intervention envelope from its recorded transitions.",
                "Removed exactly one vehicle (the ambulance) from the demand.",
                "Applied each envelope on a clock-driven policy that never reads the ambulance.",
                "Granted priority by phase selection only; no signal state was written.",
                "Paired every arm against the ambulance-free NORMAL arm.",
            ],
            tool_versions={"sumo": installation.version or "unknown"},
            limitations=[
                "SIMULATED RESULT. Not a measurement of real traffic in Bengaluru.",
                "One seed per invocation.",
                "Signal programs are netconvert-generated ESTIMATED_DATA; the effect under "
                "test may be a property of that unoptimised timing.",
                "The control demand differs from the experiment demand by one vehicle.",
            ],
        ),
        REPO_ROOT / "data" / "provenance",
    )

    print("\n=== fixed-schedule control, paired against fixed_NORMAL ===")
    print(f"{'arm':28} {'veh':>6} {'totLoss':>11} {'d totLoss':>12} {'meanLoss':>9} {'d mean':>8}")
    print(
        f"{'fixed_NORMAL':28} {base['vehicles_completed']:>6} "
        f"{base['total_time_loss_s']:>11.0f} {'—':>12} {base['mean_time_loss_s']:>9.2f} {'—':>8}"
    )
    for name in EMS_POLICIES:
        arm = runs[f"fixed_{name}"]["traffic"]
        c = comparisons[name]
        print(
            f"{'fixed_' + name:28} {arm['vehicles_completed']:>6} "
            f"{arm['total_time_loss_s']:>11.0f} {c['total_time_loss_s']['absolute']:>+12.1f} "
            f"{arm['mean_time_loss_s']:>9.2f} {c['mean_time_loss_s']['absolute']:>+8.2f}"
        )
    print(f"\nwrote {target.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
