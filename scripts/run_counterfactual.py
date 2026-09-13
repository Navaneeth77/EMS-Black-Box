#!/usr/bin/env python3
"""Phase 5 entry point: run one seed across the signal policies and pair them.

    python scripts/run_counterfactual.py --seed 42            # all four policies
    python scripts/run_counterfactual.py --seed 42 --dry-run
    python scripts/run_counterfactual.py --seed 42 --policies NORMAL EMS_NEXT

Every policy run uses the same network, demand, mix, seed, ambulance and route.
The scenario identity is computed for each run and **refused** if it differs, so a
pair whose inputs drifted cannot silently produce a number that looks like a
policy effect.

Differences are always taken against the NORMAL run of the *same* seed. Nothing
is called time saved until it has been paired that way.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation", REPO_ROOT / "analysis"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.calibration.scenario import (  # noqa: E402
    DEFAULT_VARIANT,
    UNRESOLVED_EDGE_IDS,
    ensure_variant_network,
)
from ems_sim.counterfactual.metrics import (  # noqa: E402
    intersection_attribution,
    paired_comparison,
)
from ems_sim.counterfactual.pairing import (  # noqa: E402
    ScenarioIdentity,
    policy_hash,
    require_paired,
    require_policy_difference,
)
from ems_sim.counterfactual.runner import route_tls_for, run_policy  # noqa: E402
from ems_sim.demand.ambulance import AMBULANCE_TRIPS, TWO_SIGNAL_AMBULANCE_TRIP  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.demand.generator import generate_demand  # noqa: E402
from ems_sim.disturbance.incident import DEFAULT_INCIDENT  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.policies.policies import POLICY_ORDER, make_policy  # noqa: E402
from ems_sim.policies.tls_map import summarise_route_tls  # noqa: E402
from ems_sim.provenance import (  # noqa: E402
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.sumo_env import require_sumo  # noqa: E402
from ems_sim.runner.sumo_process import SumoRunOptions  # noqa: E402

SENSITIVITY_BASIS = (
    "ASSUMED VALUE FOR SENSITIVITY TESTING — not real-world data, not a claim "
    "about any speed limit in Bengaluru, and not to be quoted as one. The value "
    "is grounded entirely inside this project's own network: of the 36 "
    "highway.primary edges in silk_board_v1, the 22 where OSM states a maxspeed "
    "state 25 km/h (2 edges), 40 km/h (4) and 60 km/h (16) — median and maximum "
    "60 — while the 14 where OSM is silent all received netconvert's 100 km/h "
    "default from its European type map. The sensitivity values are drawn from "
    "that observed set of OSM-stated values for this road class in this network, "
    "chosen before the runs and without reference to their outcome."
)


def write_speed_override_network(
    net_file: Path, edge_ids: list[str], speed_kmh: float, label: str
) -> tuple[Path, int]:
    """Copy the network with the given edges' lane speeds overridden.

    The committed network is never modified. The copy is written under a
    ``sensitivity/`` directory and named for the variant, so a sensitivity
    network cannot be picked up by a primary run.
    """
    import xml.etree.ElementTree as ET

    speed_ms = speed_kmh / 3.6
    tree = ET.parse(net_file)
    root = tree.getroot()
    wanted = set(edge_ids)
    patched = 0
    seen: set[str] = set()
    for edge in root.findall("edge"):
        if edge.get("id") not in wanted:
            continue
        seen.add(edge.get("id"))
        for lane in edge.findall("lane"):
            lane.set("speed", f"{speed_ms:.2f}")
            patched += 1
    missing = wanted - seen
    if missing:
        raise SystemExit(f"speed override named edges not in the network: {sorted(missing)}")

    target = net_file.parent / "sensitivity" / f"{net_file.stem}_{label}.net.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    tree.write(target, encoding="utf-8", xml_declaration=True)
    return target, patched


def queue_lengths_from_output(queue_file, watched: set[str]) -> dict:
    """Max and mean queueing length, from SUMO's own --queue-output.

    Reported for the ambulance's route and the incident edge separately from the
    network as a whole, because a network-wide maximum says nothing about
    whether the ambulance met a queue.
    """
    import xml.etree.ElementTree as ET

    if not queue_file.is_file():
        return {"available": False}
    network_max = route_max = 0.0
    route_total = 0.0
    route_samples = 0
    for _event, element in ET.iterparse(queue_file, events=("end",)):
        if element.tag != "lane":
            element.clear()
            continue
        length = float(element.get("queueing_length", 0.0))
        network_max = max(network_max, length)
        if element.get("id", "").rsplit("_", 1)[0] in watched:
            route_max = max(route_max, length)
            route_total += length
            route_samples += 1
        element.clear()
    return {
        "available": True,
        "network_max_queue_length_m": round(network_max, 2),
        "route_max_queue_length_m": round(route_max, 2),
        "route_mean_queue_length_m": (
            round(route_total / route_samples, 3) if route_samples else 0.0
        ),
        "source": "SUMO --queue-output queueing_length",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--policies", nargs="+", default=list(POLICY_ORDER))
    parser.add_argument("--duration", type=float, default=3600.0)
    parser.add_argument("--warmup", type=float, default=300.0)
    parser.add_argument(
        "--trip",
        default=TWO_SIGNAL_AMBULANCE_TRIP.trip_label,
        choices=sorted(AMBULANCE_TRIPS),
        help="Which committed ambulance trip to run. The earlier trips stay "
        "runnable so their published numbers can be reproduced.",
    )
    parser.add_argument(
        "--speed-override-edges",
        nargs="*",
        default=[],
        help="SENSITIVITY ONLY. Edge IDs whose lane speeds are overridden in a "
        "copy of the network. The committed network is never modified.",
    )
    parser.add_argument(
        "--speed-override-kmh",
        type=float,
        default=None,
        help="SENSITIVITY ONLY. The assumed speed for those edges, in km/h.",
    )
    parser.add_argument(
        "--sensitivity-label",
        default=None,
        help="SENSITIVITY ONLY. Names the variant. Required with a speed override; "
        "namespaces the network, demand and every output so a sensitivity run can "
        "never be mistaken for, or overwrite, a primary result.",
    )
    parser.add_argument(
        "--incident",
        action="store_true",
        help="Apply the committed lane-blockage disturbance. Identical across "
        "every policy of a seed, so a paired comparison still differs only in "
        "the policy.",
    )
    parser.add_argument(
        "--result-label",
        default="",
        help="Namespace the OUTPUT files without changing the scenario. The demand "
        "id, the routes and therefore the scenario hash stay exactly as they are, "
        "so a labelled run is the same scenario as the unlabelled one and can be "
        "compared with it directly; only the result filenames and the SUMO output "
        "directory differ. Used to re-run a frozen matrix under corrected code "
        "without overwriting the frozen results.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    installation = require_sumo()
    study_area = get_study_area(args.area)
    variant = DEFAULT_VARIANT
    net_file = ensure_variant_network(variant, study_area, REPO_ROOT, installation)

    sensitivity = None
    if args.speed_override_edges or args.speed_override_kmh is not None:
        if not (args.speed_override_edges and args.speed_override_kmh and args.sensitivity_label):
            raise SystemExit(
                "--speed-override-edges, --speed-override-kmh and --sensitivity-label "
                "must be given together."
            )
        net_file, patched = write_speed_override_network(
            net_file, args.speed_override_edges, args.speed_override_kmh, args.sensitivity_label
        )
        sensitivity = {
            "label": args.sensitivity_label,
            "edges": list(args.speed_override_edges),
            "assumed_speed_kmh": args.speed_override_kmh,
            "lanes_patched": patched,
            "data_class": "ASSUMED_SENSITIVITY_VALUE",
            "basis": SENSITIVITY_BASIS,
        }
        print(
            f"  SENSITIVITY: {sensitivity['label']} — {args.speed_override_edges} "
            f"-> {args.speed_override_kmh} km/h ({patched} lanes patched)"
        )
        print("  This is an ASSUMED value for sensitivity testing, not real-world data.")

    ambulance = AMBULANCE_TRIPS[args.trip]

    config = make_config(
        period=DemandPeriod.EVENING_PEAK,
        seed=args.seed,
        duration_s=args.duration,
        warmup_s=args.warmup,
    )
    from dataclasses import replace

    incident = DEFAULT_INCIDENT if args.incident else None
    prefix = f"sens_{args.sensitivity_label}_" if sensitivity else ""
    if incident is not None:
        prefix = f"inc_{prefix}"
    config = replace(config, demand_id=f"cf_{prefix}{args.area}_{args.trip}_seed{args.seed}")
    # The label names the *analysis*, never the scenario: it is deliberately not
    # part of demand_id, so a labelled run reads the same routes file and hashes
    # to the same scenario as the run it is being compared with.
    label = f"{args.result_label.strip('_')}_" if args.result_label else ""

    print(f"Counterfactual replay — {args.area}, trip {args.trip}, seed {args.seed}")
    print(f"  network   : {net_file.relative_to(REPO_ROOT)}")
    print(f"  sha256    : {sha256_file(net_file)[:32]}…")
    print(f"  demand    : {config.demand_id}  hash {config.config_hash()}")
    print(
        f"  window    : {config.begin_s:.0f}-{config.end_s:.0f}s  step "
        f"{config.step_length_s}s  scale {config.demand_scale:g}"
    )
    print(
        f"  ambulance : {ambulance.origin_edge} -> {ambulance.destination_edge} "
        f"at t={ambulance.depart_time_s:.0f}s"
    )
    print(f"  variant   : {variant.variant_id} (unresolved {UNRESOLVED_EDGE_IDS} included)")
    print(f"  policies  : {args.policies}")

    # Generate the demand once. Every policy run uses the same route file, so the
    # demand cannot differ between them by construction.
    demand = generate_demand(config, ambulance, net_file, REPO_ROOT, installation)
    route = demand.ambulance_route_edges
    route_tls = route_tls_for(net_file, route)
    summary = summarise_route_tls(route_tls)

    print(f"\n  ambulance route: {len(route)} edges")
    print(
        f"  traffic lights on route: {summary['traffic_lights_on_route']} "
        f"({summary['actionable']} actionable)"
    )
    for entry in summary["detail"]:
        flag = "ACTIONABLE" if entry["has_red_exposure"] else "permanently green"
        print(
            f"    {entry['tls_id'][:52]:54} links={entry['ambulance_link_indices']} "
            f"green={entry['green_fraction_for_ambulance']:.3f}  {flag}"
        )
    uses_unresolved = sorted(set(route) & set(UNRESOLVED_EDGE_IDS))
    print(f"  ambulance route uses unresolved infrastructure: {uses_unresolved or 'no'}")

    if args.dry_run:
        print("\n[dry run] Nothing simulated.")
        return 0

    identity_for = lambda: ScenarioIdentity(  # noqa: E731
        network_sha256=sha256_file(net_file),
        demand_config_hash=config.config_hash(),
        demand_id=config.demand_id,
        seed=args.seed,
        begin_s=config.begin_s,
        end_s=config.end_s,
        step_length_s=config.step_length_s,
        vehicle_mix=dict(config.vehicle_mix),
        ambulance_origin=ambulance.origin_edge,
        ambulance_destination=ambulance.destination_edge,
        ambulance_depart_s=ambulance.depart_time_s,
        ambulance_route_edges=tuple(route),
        scenario_variant=variant.variant_id,
        # The disturbance belongs to the scenario: a policy compared against a
        # baseline with no lane blocked is not being compared against its own
        # scenario, and without this the two would hash identically.
        disturbance=(
            tuple(sorted(incident.as_dict().items())) if incident is not None else ()
        ),
    )

    results = {}
    identities = {}
    started = time.monotonic()
    for name in args.policies:
        print(f"\n=== {name} ===", flush=True)
        output_dir = (
            REPO_ROOT / "simulation" / "results" / f"{config.demand_id}_{label}{name}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        options = SumoRunOptions(
            net_file=net_file,
            route_files=(demand.routes_file,),
            begin_s=config.begin_s,
            end_s=config.end_s,
            step_length_s=config.step_length_s,
            seed=args.seed,
            tripinfo_output=output_dir / "tripinfo.xml",
            statistic_output=output_dir / "statistics.xml",
            queue_output=output_dir / "queues.xml",
        )
        result = run_policy(
            options,
            make_policy(name),
            ambulance.vehicle_id,
            net_file,
            route,
            installation,
            incident=incident,
        )
        result.output_dir = str(output_dir.relative_to(REPO_ROOT))
        from ems_sim.runner.traci_bridge import (
            enrich_from_statistics,
            enrich_from_tripinfo,
        )

        enrich_from_statistics(result.measurements, output_dir / "statistics.xml")
        enrich_from_tripinfo(result.measurements, output_dir / "tripinfo.xml")
        # Queue length in metres comes from SUMO's own queue output. The TraCI
        # series carries halting counts; it must not be asked for lengths.
        result.queue_report = queue_lengths_from_output(
            output_dir / "queues.xml",
            watched=set(route) | ({incident.edge_id} if incident else set()),
        )
        results[name] = result
        identities[name] = identity_for()

        trip = result.ambulance
        valid, reason = result.valid_for_headline
        print(
            f"  departed={result.measurements.departed} "
            f"arrived={result.measurements.arrived} "
            f"backlog={result.measurements.insertion_backlog_at_end} "
            f"teleports={len(result.measurements.teleports)}"
        )
        print(
            f"  ambulance travel={trip.travel_time_s if trip else None}s "
            f"wait={trip.waiting_time_s if trip else None}s "
            f"loss={trip.time_loss_s if trip else None}s "
            f"stops={result.ambulance_stop_count}"
        )
        print(
            f"  policy: {result.policy_report['transition_count']} state transitions, "
            f"{result.policy_report['signal_change_count']} signal changes, "
            f"{len(result.signal_conflicts)} conflicts"
        )
        print(f"  valid for headline: {valid} ({reason})")

    elapsed = time.monotonic() - started

    # --- pair every policy against NORMAL of the same seed ------------------
    comparisons = {}
    attributions = {}
    if "NORMAL" in results:
        normal = results["NORMAL"]
        for name, result in results.items():
            if name == "NORMAL":
                continue
            require_paired(identities["NORMAL"], identities[name], "NORMAL", name)
            require_policy_difference(
                results["NORMAL"].policy_report, results[name].policy_report
            )
            comparisons[name] = paired_comparison(normal, result)
            attributions[name] = intersection_attribution(normal, result)

    ems_dir = REPO_ROOT / "data" / "processed" / args.area / "ems"
    cf_dir = (
        REPO_ROOT
        / "data"
        / "processed"
        / args.area
        / ("sensitivity" if sensitivity else "counterfactual")
    )
    ems_dir.mkdir(parents=True, exist_ok=True)
    cf_dir.mkdir(parents=True, exist_ok=True)

    reproducibility = {
        "generated_at": utc_now_iso(),
        "sumo_version": installation.version,
        "network_file": str(net_file.relative_to(REPO_ROOT)),
        "network_sha256": sha256_file(net_file),
        "scenario_hash": identities[args.policies[0]].scenario_hash(),
        "scenario": identities[args.policies[0]].as_dict(),
        "policy_hashes": {
            name: policy_hash(result.policy_report) for name, result in results.items()
        },
        "demand_config": config.as_dict(),
        "ambulance": ambulance.as_dict(),
        "scenario_variant": variant.as_dict(),
        "wall_clock_total_s": round(elapsed, 1),
        "sensitivity": sensitivity,
        "incident": incident.as_dict() if incident else None,
    }

    (ems_dir / f"route_traffic_lights_{label}{prefix}{args.trip}_seed{args.seed}.json").write_text(
        json.dumps({"reproducibility": reproducibility, "summary": summary}, indent=2) + "\n",
        encoding="utf-8",
    )
    for name, result in results.items():
        (cf_dir / f"{label}{prefix}{args.trip}_seed{args.seed}_{name}.json").write_text(
            json.dumps({"reproducibility": reproducibility, "run": result.as_dict()}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    (cf_dir / f"{label}{prefix}{args.trip}_seed{args.seed}_comparisons.json").write_text(
        json.dumps(
            {
                "reproducibility": reproducibility,
                "comparisons": comparisons,
                "intersection_attribution": attributions,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    write_record(
        ProvenanceRecord(
            dataset_id=f"phase5_counterfactual_{label}{prefix}{args.trip}_seed{args.seed}",
            data_class=DataClass.SIMULATED,
            description=(
                f"Phase 5 counterfactual replay for {args.area}, seed {args.seed}: "
                f"{len(results)} signal policies over one identical scenario, paired "
                f"against the NORMAL run of the same seed."
            ),
            produced_by=f"scenario:{reproducibility['scenario_hash']} seed:{args.seed}",
            random_seed=args.seed,
            source_name="EMS Black Box Phase 5",
            retrieved_at=utc_now_iso(),
            file_path=str(
                (
                    cf_dir / f"{label}{prefix}{args.trip}_seed{args.seed}_comparisons.json"
                ).relative_to(REPO_ROOT)
            ),
            derived_from=[
                "phase3_baseline",
                "phase4_multi_seed_include_unresolved",
                "silk_board_v1_sumo_network",
                "sumo_network_review",
            ],
            processing_steps=[
                "Generated the demand once; every policy run used the same route file.",
                "Identified the traffic lights on the ambulance route and the link "
                "indices its own movement uses.",
                "Ran each policy under TraCI, observing the ambulance and never moving it.",
                "Validated every watched signal state against its program each step.",
                "Verified scenario identity between paired runs before comparing.",
                "Computed differences against NORMAL of the same seed only.",
            ],
            tool_versions={"sumo": installation.version or "unknown"},
            limitations=[
                "SIMULATED RESULT. Not a measurement of any real ambulance journey "
                "in Bengaluru. No ambulance GPS trace, dispatch record or observed "
                "travel time was used.",
                "Signal programs are netconvert-generated (ESTIMATED_DATA), so the "
                "baseline these policies are measured against is assumed timing, "
                "not verified controller timing.",
                "Demand rates, vehicle mix and vehicle behaviour are ESTIMATED_DATA "
                "and are not field-calibrated.",
                "1,375 of 1,515 edge speeds are SUMO defaults; 166 are at or above "
                "80 km/h. Time loss is measured against free-flow, so it is "
                "over-estimated on those edges.",
                "The ambulance trip is a configuration choice, not a real dispatch. "
                "It was reselected in Phase 5 because the Phase 3 trip's route "
                "passes no traffic lights at all.",
                f"way/1351994264 remains operational-status UNRESOLVED and is "
                f"included in this variant. Ambulance route uses it: "
                f"{bool(uses_unresolved)}.",
                "The sublane model is deliberately not enabled, so two-wheeler "
                "lateral behaviour is inert (Phase 4 finding).",
                "Single seed. Seeds 43-46 have not been run.",
            ],
            notes=(
                f"Policies: {list(results)}. Wall clock {elapsed:.0f}s. "
                f"Scenario hash {reproducibility['scenario_hash']}."
            ),
        ),
        REPO_ROOT / "data" / "provenance",
    )

    print(f"\n=== paired comparisons vs NORMAL (seed {args.seed}) ===")
    print(
        f"{'policy':22} {'ambTT':>8} {'saved':>8} {'amb%':>7} {'trafficLoss':>12} "
        f"{'net':>10} {'trans':>6} {'chg':>5} {'confl':>6}"
    )
    for name, comparison in comparisons.items():
        travel = comparison["ambulance"]["travel_time"]
        report = results[name].policy_report
        print(
            f"{name:22} {travel['policy']:>8.1f} "
            f"{comparison['ambulance_time_saved_s']:>+8.2f} "
            f"{comparison['ambulance_improvement_percent']:>+7.2f} "
            f"{comparison['traffic_delay_cost_s']:>+12.1f} "
            f"{comparison['net_system_impact_s']:>+10.1f} "
            f"{report['transition_count']:>6} {report['signal_change_count']:>5} "
            f"{len(results[name].signal_conflicts):>6}"
        )
    if "NORMAL" in results:
        trip = results["NORMAL"].ambulance
        print(
            f"{'NORMAL (baseline)':22} {trip.travel_time_s:>8.1f} "
            f"{'—':>8} {'—':>7} {'—':>12} {'—':>10} {0:>6} {0:>5} "
            f"{len(results['NORMAL'].signal_conflicts):>6}"
        )

    print(f"\nwrote {cf_dir.relative_to(REPO_ROOT)}/ and {ems_dir.relative_to(REPO_ROOT)}/")
    print(
        "\nSimulated results. Under this simulation scenario only — not a "
        "measurement of\nreal ambulance performance in Bengaluru."
    )
    print("\nSTOP: seeds 43-46 have NOT been run. Review seed 42 before continuing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
