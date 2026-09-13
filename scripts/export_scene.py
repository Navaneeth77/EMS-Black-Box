#!/usr/bin/env python3
"""Phase 7: export a committed SUMO run as a 3D scene the frontend can render.

    python scripts/export_scene.py --seed 42 --trip two_signal --policy NORMAL

Runs SUMO once with FCD enabled, then writes scene-space geometry and state to
``frontend/public/scene/``. Nothing here simulates and nothing is invented: the
roads are SUMO lane shapes, the vehicles are SUMO FCD samples, the signal
programs are the network's own, and the two things that *are* reconstructed —
elevation and building heights — are labelled ESTIMATED_DATA at the point of
use, not in a footnote.

The FCD run is a **separate** run from the committed counterfactual results and
writes to its own directory. It reproduces them rather than replacing them: same
network, same demand, same seed, same policy, so the ambulance's trajectory here
is the trajectory behind the published numbers.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "simulation",):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from ems_sim.calibration.scenario import DEFAULT_VARIANT, ensure_variant_network  # noqa: E402
from ems_sim.counterfactual.runner import route_tls_for, run_policy  # noqa: E402
from ems_sim.demand.ambulance import AMBULANCE_TRIPS  # noqa: E402
from ems_sim.demand.config import DemandPeriod, make_config  # noqa: E402
from ems_sim.demand.generator import generate_demand  # noqa: E402
from ems_sim.disturbance.scenarios import DEMO_INCIDENT_SET, RESEARCH_INCIDENT  # noqa: E402
from ems_sim.historical.demand import (  # noqa: E402
    QUEUE_HALT_WINDOW_M,
    TIME_TO_TELEPORT_S,
    conversion_record,
    generate_historical_demand,
)
from ems_sim.historical.network import TLS_ID as HDEMO_TLS_ID  # noqa: E402
from ems_sim.historical.network import VARIANT_ID as HDEMO_VARIANT_ID  # noqa: E402
from ems_sim.historical.network import demo_net_file  # noqa: E402
from ems_sim.historical.policy import (  # noqa: E402
    demo_run_options,
    make_historical_policy,
)
from ems_sim.historical.sources import data_dir as historical_data_dir  # noqa: E402
from ems_sim.historical.sources import load_observations  # noqa: E402
from ems_sim.historical.trip import SEED as HDEMO_SEED  # noqa: E402
from ems_sim.historical.trip import trip_config as historical_trip_config  # noqa: E402
from ems_sim.network.study_area import get_study_area  # noqa: E402
from ems_sim.policies.policies import make_policy  # noqa: E402
from ems_sim.provenance import (  # noqa: E402
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.sumo_env import require_sumo, sumo_tools_on_path  # noqa: E402
from ems_sim.runner.sumo_process import SumoRunOptions  # noqa: E402
from ems_sim.viz.buildings import (  # noqa: E402
    clip_to_carriageway,
    drop_carriageway_overlaps,
    export_buildings,
)
from ems_sim.viz.coords import SceneTransform  # noqa: E402
from ems_sim.viz.elevation import ElevationModel  # noqa: E402
from ems_sim.viz.network_export import (  # noqa: E402
    LayerIndex,
    export_network,
    export_traffic_lights,
)
from ems_sim.viz.trajectory_export import export_trajectories, lane_elevation_map  # noqa: E402


def write_json(path: Path, payload: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(text)




def intersection_camera(tls: dict, centre: list[float], ambulance_links: list[int]) -> dict:
    """Where to stand to watch the ambulance's approach and the junction at once.

    Computed here, from the controller's own stop-line geometry, so the scene, the
    frontend camera and the validator's line-of-sight check all use one definition.
    Behind the ambulance's own stop line, off to its right, high enough to see over
    the queue: the shot looks along the corridor rather than down onto it.
    """
    links = [link for link in tls["links"] if link["index"] in ambulance_links] or tls["links"]
    sx = sum(link["position"][0] for link in links) / len(links)
    sz = sum(link["position"][2] for link in links) / len(links)
    heading = math.radians(links[0]["heading"])
    travel = (math.sin(heading), -math.cos(heading))
    right = (math.cos(heading), math.sin(heading))
    return {
        "position": [
            round(sx - travel[0] * 130 + right[0] * 38, 2),
            round(centre[1] + 46, 2),
            round(sz - travel[1] * 130 + right[1] * 38, 2),
        ],
        "target": [
            round(sx - travel[0] * 30, 2),
            round(centre[1] + 2, 2),
            round(sz - travel[1] * 30, 2),
        ],
        "stop_line": [round(sx, 2), round(centre[1], 2), round(sz, 2)],
        "basis": "130 m back down the ambulance's approach, 38 m to its right, 46 m up.",
    }


def first_queue_halt(events: list[dict], tls_id: str) -> dict | None:
    """The ambulance's first halt in that signal's queue, as the run recorded it."""
    for event in events:
        queue = event.get("queue_ahead")
        distance = (event.get("distance_to_tls_m") or {}).get(tls_id)
        if queue is None or distance is None or distance > QUEUE_HALT_WINDOW_M:
            continue
        return {
            "sim_time_s": event["sim_time_s"],
            "edge_id": event["edge_id"],
            "distance_to_stop_line_m": distance,
            "vehicles_ahead": queue["count"],
            "vehicles_ahead_halted": queue["halted"],
            "stopped_by_signal": event["stopped_by_signal"],
        }
    return None


def priority_requests(transitions: list[dict], decisions: list[dict] | None = None) -> list[dict]:
    """Where the ambulance was when priority was first requested at each signal.

    The decision the policy took is carried with it — the distance it was at, its
    speed, and the activation distance that speed implied — so the demo can show
    the request as the arithmetic it was rather than as a time stamp.
    """
    by_request = {(d["tls_id"], d["sim_time_s"]): d for d in decisions or []}
    seen: dict[str, dict] = {}
    for transition in transitions:
        if transition["new_state"] != "REQUESTED" or transition["tls_id"] in seen:
            continue
        decision = by_request.get((transition["tls_id"], transition["sim_time_s"]), {})
        seen[transition["tls_id"]] = {
            "tls_id": transition["tls_id"],
            "sim_time_s": transition["sim_time_s"],
            "distance_to_stop_line_m": transition.get("ambulance_distance_to_tls_m"),
            "ambulance_speed_ms": transition.get("ambulance_speed_ms"),
            "activation_distance_m": decision.get("activation_distance_m"),
            "eta_s": decision.get("eta_s"),
            "lead_time_s": decision.get("lead_time_s"),
            "transition_s": decision.get("transition_s"),
            "queue_clearance_s": decision.get("clearance_s"),
            "reason": transition["reason"],
        }
    return list(seen.values())


HDEMO_DATA = REPO_ROOT / "data" / "processed" / "historical_demo"


def load_historical_inputs(args) -> dict:
    """Everything HISTORICAL_DEMO reads, checked for consistency before anything runs.

    The network, the rule-selected trip and the sweep-selected demand scale were
    produced in that order. If any of them was rebuilt without the others, the
    scene would silently combine a trip chosen on one network with another, so
    each record's network hash is checked against the file on disk.
    """
    if args.seed != HDEMO_SEED:
        raise SystemExit(
            f"HISTORICAL_DEMO's trip and demand scale were selected with seed {HDEMO_SEED}; "
            f"got --seed {args.seed}."
        )
    net_file = demo_net_file(REPO_ROOT)
    network_record = json.loads((net_file.parent / "network_provenance.json").read_text())
    net_sha = sha256_file(net_file)
    if not network_record.get("passed") or network_record["demo_network"]["sha256"] != net_sha:
        raise SystemExit("HISTORICAL_DEMO network failed or changed after its build checks.")
    selection = json.loads((HDEMO_DATA / "trip_selection.json").read_text())
    sweep = json.loads((HDEMO_DATA / "demand_scale_sweep.json").read_text())
    if selection["network_sha256"] != net_sha or sweep["network_sha256"] != net_sha:
        raise SystemExit("Trip selection or demand sweep ran on a different network; rerun them.")
    k = args.hdemo_k if args.hdemo_k is not None else sweep.get("selected_k")
    depart_s = sweep.get("selected_depart_s")
    if k is None or depart_s is None:
        raise SystemExit(
            "The scenario sweep selected no usable demand scale and departure. "
            "Run scripts/historical_scenario_sweep.py."
        )
    return {
        "net_file": net_file,
        "network_record": network_record,
        "selection": selection,
        "sweep": sweep,
        "k": float(k),
        "k_is_selected": args.hdemo_k is None,
        "obs": load_observations(REPO_ROOT),
        "depart_s": float(depart_s),
        "ambulance": historical_trip_config(
            selection["chosen"]["origin"],
            selection["chosen"]["destination"],
            float(depart_s),
        ),
    }


def apply_historical_manifest(manifest: dict, hdemo: dict, config, demand, traffic_lights) -> None:
    """Add the snapshot, the four-way controller and the labels HISTORICAL_DEMO shows.

    Every displayed string is built here from the observed file, the provenance
    file and the network record, so the viewer shows what the data says rather
    than text maintained separately in the frontend.
    """
    obs = hdemo["obs"]
    k = hdemo["k"]
    sweep = hdemo["sweep"]
    data_dir = historical_data_dir(REPO_ROOT)
    provenance = json.loads((data_dir / "provenance.json").read_text())
    sources = {s["source_id"]: s for s in provenance["sources"]}
    primary = sources["CMP2019_DRAFT"]
    secondary = sources["IJIRSET2017"]
    survey = sources["RMP2031_DRAFT_VOL3"]
    sumo_vph = sum(f.vehicles_per_hour for f in config.scaled_flows())

    record = conversion_record(
        obs,
        k,
        config,
        demand,
        scale_selection={
            "rule": sweep["rule"],
            "grid": sweep["grid"],
            "selected_k": sweep["selected_k"],
            "k_used": k,
            "rows": [
                {
                    key: row[key]
                    for key in (
                        "k", "depart_s", "valid", "usable", "teleports", "departed",
                        "insertion_backlog_at_end", "sumo_input_vph",
                        "vehicles_ahead_at_first_halt",
                    )
                }
                for row in sweep["rows"]
            ],
            "record": "data/processed/historical_demo/demand_scale_sweep.json",
        },
    )
    if hdemo["k_is_selected"]:
        (data_dir / "demand_conversion.json").write_text(json.dumps(record, indent=2) + "\n")

    manifest["mode"] = "HISTORICAL_DEMO"
    manifest["demo_note"] = (
        "HISTORICAL_DEMO: a SUMO replay generated from historical observed demand at Central "
        "Silk Board. Vehicle motion, signal states, queues and travel times are SIMULATED; "
        "they are not historical GPS traces or signal logs. Not the research result."
    )
    manifest["scenario"]["trip"] = "historical_demo"
    manifest["scenario"]["variant"] = HDEMO_VARIANT_ID
    manifest["demand_config_hash"] = config.config_hash()
    manifest["historical"] = {
        "title": "HISTORICAL TRAFFIC SNAPSHOT",
        "location": f"{obs.location['name']}, Bengaluru ({obs.location['junction_type']})",
        "survey_date": "Dec 2014 – Apr 2015 survey campaign (exact day not reported)",
        "survey_period": "Peak hour of a 24-hour classified turning count "
        "(clock time not reported)",
        "observed": [
            {
                "label": "OBSERVED",
                "text": f"{obs.peak_hour_vehicles:,.0f} vehicles in the peak hour",
            },
            {"label": "OBSERVED", "text": f"{obs.peak_hour_pcu:,.0f} PCU in the peak hour"},
            {"label": "OBSERVED", "text": f"{obs.daily_vehicles:,.0f} vehicles in 24 hours"},
        ],
        "composition": [
            {
                "label": "OBSERVED",
                "text": f"Cars {obs.car_share:.0%} of vehicles (IJIRSET 2017; survey date not "
                "reported)",
            },
            {
                "label": "NOT REPORTED",
                "text": "Other classes: no published breakdown for Silk Board",
            },
        ],
        "signal": {
            "label": "OBSERVED",
            "text": f"Existing cycle length {obs.cycle_length_s:.0f} s (IJIRSET 2017)",
        },
        "source": {
            "text": f"{primary['title']}, Table 2-13 (from {survey['title'].split(':')[0]})",
            "url": primary["url"],
            "secondary_text": f"{secondary['title']}, IJIRSET {secondary['publication_date']}",
            "secondary_url": secondary["url"],
        },
        "conversion": {
            "label": "ESTIMATED",
            "k": k,
            "sumo_input_vph": round(sumo_vph, 1),
            "text": f"SUMO demand = {obs.peak_hour_vehicles:,.0f} veh/h x {k:g} = "
            f"{sumo_vph:,.0f} veh/h (what the modelled network carries, and enough "
            f"traffic for the queue the demo shows)",
        },
        "replay_statement": "SUMO replay generated from historical observed demand",
        "not_gps": "Vehicles are simulated by SUMO. They are not historical GPS traces.",
        "files": {
            "observed_counts": "data/traffic/historical_central_silk_board/observed_counts.json",
            "provenance": "data/traffic/historical_central_silk_board/provenance.json",
            "source_notes": "data/traffic/historical_central_silk_board/source_notes.md",
            "demand_conversion": str(
                (data_dir / "demand_conversion.json").relative_to(REPO_ROOT)
            ),
            "trip_selection": "data/processed/historical_demo/trip_selection.json",
            "demand_scale_sweep": "data/processed/historical_demo/demand_scale_sweep.json",
            "network_provenance": str(
                (hdemo["net_file"].parent / "network_provenance.json").relative_to(REPO_ROOT)
            ),
        },
    }

    # Road names for the signals on the route, from the network's own edge names,
    # so the viewer can say "signal on Hosur Road" instead of a controller id.
    import sumolib

    net = sumolib.net.readNet(str(hdemo["net_file"]))
    for entry in manifest["route_traffic_lights"]:
        entry["approach_road"] = net.getEdge(entry["approach_edge"]).getName() or ""

    controller = hdemo["network_record"]["controller"]
    tls = next(t for t in traffic_lights if t["id"] == controller["tls_id"])
    positions = [link["position"] for link in tls["links"]]
    manifest["intersection"] = {
        "tls_id": controller["tls_id"],
        "name": "Central Silk Board four-way (at-grade)",
        "centre": [round(sum(p[i] for p in positions) / len(positions), 2) for i in range(3)],
        "approaches": controller["approaches"],
        "phases": controller["phases"],
        "cycle_length_s": sum(p["duration_s"] for p in controller["phases"]),
        "timing_provenance": controller["timing_provenance"],
        "replaces_separate_tls_ids": controller["replaces_separate_tls_ids"],
        "on_ambulance_route": any(
            t["tls_id"] == controller["tls_id"] for t in manifest["route_traffic_lights"]
        ),
    }
    own = next(
        (t for t in manifest["route_traffic_lights"] if t["tls_id"] == controller["tls_id"]),
        None,
    )
    manifest["intersection"]["camera"] = intersection_camera(
        tls,
        manifest["intersection"]["centre"],
        own["ambulance_link_indices"] if own else [],
    )
    manifest["provenance"].update(
        {
            "traffic_demand": (
                f"Total = OBSERVED Silk Board peak hour {obs.peak_hour_vehicles:,.0f} veh/h x "
                f"ESTIMATED k {k:g}; car share OBSERVED {obs.car_share:.0%}; OD spread and "
                "non-car split ESTIMATED"
            ),
            "signal_programs": (
                f"{controller['tls_id']}: cycle {obs.cycle_length_s:.0f} s OBSERVED "
                "(IJIRSET 2017); split, yellow, all-red and order ESTIMATED. Other signals: "
                "netconvert-generated ESTIMATED_DATA"
            ),
            "signal_states": "SIMULATED_DATA (SUMO TraCI, recorded on change)",
            "vehicle_positions": "SIMULATED_DATA (SUMO FCD). Not historical GPS traces.",
            "road_elevation": "ESTIMATED_DATA (OSM layer ordinal x 6 m, ramped at structure ends)",
            "building_footprints": (
                "PUBLICLY_SOURCED_DATA (OSM building ways). Buildings have no vertical "
                "placement here, so a footprint over the road would be extruded through "
                "it: one covering a road is not drawn, and every other one is trimmed "
                "back off the drivable surface. See buildings.json carriageway_overlap "
                "and carriageway_clip."
            ),
            "disturbance": "none injected",
        }
    )
    manifest["not_a_real_world_claim"] = (
        "HISTORICAL_DEMO renders a SUMO simulation whose demand is anchored to historical "
        "observed counts at Central Silk Board (junction volume from the Dec 2014 - Apr 2015 "
        "survey campaign; car share and cycle length reported in 2017). Vehicle positions, "
        "signal states, queues and travel times are SIMULATED. They are not historical GPS "
        "traces, observed signal logs, or a real ambulance journey."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--area", default="silk_board_v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--trip", default="two_signal", choices=sorted(AMBULANCE_TRIPS))
    parser.add_argument("--policy", default="NORMAL")
    parser.add_argument("--duration", type=float, default=3600.0)
    parser.add_argument("--warmup", type=float, default=300.0)
    parser.add_argument(
        "--window",
        nargs=2,
        type=float,
        default=[540.0, 900.0],
        metavar=("BEGIN", "END"),
        help="Simulation-time window to export trajectories for. Defaults to "
        "540-900 s, which brackets the ambulance trip (departs 600 s). Clipping "
        "is a file-size decision and never alters a value inside the window.",
    )
    parser.add_argument("--fcd-period", type=float, default=0.5)
    parser.add_argument(
        "--demo-scale",
        type=float,
        default=0.8,
        help="DEMO MODE demand scale. Research uses 0.5; 1.0 gridlocks this network.",
    )
    parser.add_argument("--max-buildings", type=int, default=2500)
    parser.add_argument(
        "--incident",
        action="store_true",
        help="Apply the frozen research disturbance (single obstruction).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="DEMO MODE. Denser demand, the four-point demo disturbance set, and "
        "the run stops at the ambulance's arrival. A different scenario from the "
        "frozen research experiment, with its own hash and output namespace; it "
        "never writes a research output.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Subdirectory of frontend/public/scene to write to. Empty writes the "
        "primary scene; 'compare' writes the pane the comparison view loads. Two "
        "exports must not share a directory or the second silently replaces the first.",
    )
    parser.add_argument("--skip-sumo", action="store_true", help="Reuse an existing FCD file.")
    parser.add_argument(
        "--historical",
        action="store_true",
        help="HISTORICAL_DEMO. Demand anchored to the observed Silk Board peak hour, the "
        "four-way controller network variant, the rule-selected trip, no disturbance, "
        "stopped at arrival. Writes under frontend/public/scene/historical and never "
        "writes a research output.",
    )
    parser.add_argument(
        "--hdemo-k",
        type=float,
        default=None,
        help="Override HISTORICAL_DEMO's demand scale k (default: the sweep's selection).",
    )
    args = parser.parse_args()

    installation = require_sumo()
    # sumolib lives inside the SUMO installation, not in the venv. Without this
    # the geometry exporters only work when a simulation has already run and put
    # the tools directory on the path as a side effect — which --skip-sumo does
    # not do.
    tools = sumo_tools_on_path(installation)
    if tools not in sys.path:
        sys.path.insert(0, tools)

    study_area = get_study_area(args.area)
    variant = DEFAULT_VARIANT
    net_file = ensure_variant_network(variant, study_area, REPO_ROOT, installation)
    ambulance = AMBULANCE_TRIPS[args.trip]
    hdemo = load_historical_inputs(args) if args.historical else None
    if hdemo is not None:
        net_file = hdemo["net_file"]
        ambulance = hdemo["ambulance"]
        if "--fcd-period" not in sys.argv:
            # At this demand a 0.5 s recording is ~100 MB per scene, and the
            # comparison view parses two of them. The rate is an output setting: it
            # changes nothing in the simulation, and the renderer already
            # interpolates between recorded samples.
            args.fcd_period = 1.0

    from dataclasses import replace

    config = make_config(
        period=DemandPeriod.EVENING_PEAK,
        seed=args.seed,
        duration_s=args.duration,
        warmup_s=args.warmup,
    )
    config = replace(config, demand_id=f"cf_{args.area}_{args.trip}_seed{args.seed}")
    if args.demo:
        # DEMO MODE demand. Denser than the research model so the arterial reads
        # as busy, but still inside the envelope Phase 4 established: scale 1.0
        # gridlocked this network (148 teleports, 955 vehicles that never
        # entered), so the demo stays below it. This is a *different scenario*,
        # not a recalibration of the research one - hence its own demand_id.
        config = replace(
            config,
            demand_scale=args.demo_scale,
            demand_id=f"demo_{args.area}_{args.trip}_seed{args.seed}_s{args.demo_scale:g}",
        )

    demand = None
    if hdemo is not None:
        # HISTORICAL_DEMO injects no disturbance: congestion comes only from the
        # demand anchored to the observed count and from the signal program.
        incident = None
        tag = f"hdemo_{args.policy}"
        demand = generate_historical_demand(
            hdemo["obs"],
            hdemo["k"],
            ambulance,
            net_file,
            REPO_ROOT,
            seed=args.seed,
            installation=installation,
        )
        config = demand.config
    elif args.demo:
        incident = DEMO_INCIDENT_SET
        tag = f"demo_{args.policy}"
    elif args.incident:
        incident = RESEARCH_INCIDENT
        tag = f"inc_{args.policy}"
    else:
        incident = None
        tag = args.policy
    out_dir = REPO_ROOT / "simulation" / "results" / f"{config.demand_id}_fcd_{tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    fcd_file = out_dir / "fcd.xml"

    print(f"Scene export — {args.area}, trip {args.trip}, seed {args.seed}, policy {args.policy}")
    if demand is None:
        demand = generate_demand(config, ambulance, net_file, REPO_ROOT, installation)
    route = demand.ambulance_route_edges

    if not args.skip_sumo or not fcd_file.is_file():
        print(f"  running SUMO with FCD every {args.fcd_period}s ...", flush=True)
        options = SumoRunOptions(
            net_file=net_file,
            route_files=(demand.routes_file,),
            begin_s=config.begin_s,
            end_s=config.end_s,
            step_length_s=config.step_length_s,
            seed=args.seed,
            tripinfo_output=out_dir / "tripinfo.xml",
            statistic_output=out_dir / "statistics.xml",
            queue_output=out_dir / "queues.xml",
            fcd_output=fcd_file,
            fcd_period_s=args.fcd_period,
            **({"time_to_teleport_s": TIME_TO_TELEPORT_S} if hdemo is not None else {}),
            # HISTORICAL_DEMO's EMS runs equip the ambulance with SUMO's
            # bluelight device, so the traffic in front of it responds to the
            # siren instead of only to the signal. Command line only: the routes
            # file stays byte-identical to the NORMAL run's.
            # See historical.policy.bluelight_run_options.
            extra=(
                demo_run_options(args.policy, ambulance.vehicle_id)
                if hdemo is not None
                else {}
            ),
        )
        policy = (
            make_historical_policy(args.policy) if hdemo is not None else make_policy(args.policy)
        )
        result = run_policy(
            options,
            policy,
            ambulance.vehicle_id,
            net_file,
            route,
            installation,
            incident=incident,
            stop_on_ambulance_arrival=args.demo or hdemo is not None,
            record_signal_timeline=True,
            # HISTORICAL_DEMO's policy reasons about arrival at the stop line, and
            # its record shows how much traffic stood between the ambulance and the
            # signal. Both are off for research runs.
            measure_distance_to_stop_line=hdemo is not None,
            record_queue_ahead=hdemo is not None,
        )
        # tripinfo carries waiting time and time loss; without this the summary
        # would disagree with the committed counterfactual result for the same
        # seed and policy, and look like the FCD run had changed the simulation.
        from ems_sim.runner.traci_bridge import enrich_from_statistics, enrich_from_tripinfo

        enrich_from_statistics(result.measurements, options.statistic_output)
        enrich_from_tripinfo(result.measurements, options.tripinfo_output)
        trip = result.ambulance
        print(
            f"  ambulance travel={trip.travel_time_s if trip else None}s "
            f"wait={trip.waiting_time_s if trip else None}s "
            f"conflicts={len(result.signal_conflicts)}"
        )
        ambulance_summary = {
            "vehicle_id": ambulance.vehicle_id,
            "arrived_at_s": result.ambulance_arrived_at_s,
            "travel_time_s": trip.travel_time_s if trip else None,
            "waiting_time_s": trip.waiting_time_s if trip else None,
            "time_loss_s": trip.time_loss_s if trip else None,
            "route_edges": result.ambulance_route_edges,
            "signal_wait_events": result.ambulance_signal_waits,
            "depart_time_s": ambulance.depart_time_s,
            "stops": result.ambulance_stop_count,
        }
        policy_transitions = result.policy_report["state_transitions"]
        if hdemo is not None:
            # What the demo claims on screen — the ambulance was caught behind a
            # queue, and priority was asked for before it got there — read back out
            # of the run that produced it.
            ambulance_summary["queue_joined"] = first_queue_halt(
                result.ambulance_signal_waits, HDEMO_TLS_ID
            )
            # The policy files its decisions under its own parameters block.
            ambulance_summary["priority_requests"] = priority_requests(
                policy_transitions, result.policy_report["parameters"].get("decisions")
            )
            ambulance_summary["policy_parameters"] = result.policy_report["parameters"]
        signal_timeline = result.signal_timeline
        incident_summary = result.incident_report
        queue_summary_data = {**result.as_dict()["queues"]}
        run_quality = {
            "teleports": len(result.measurements.teleports),
            "signal_conflicts": len(result.signal_conflicts),
            "insertion_backlog_at_end": result.measurements.insertion_backlog_at_end,
            "departed": result.measurements.departed,
            "arrived": result.measurements.arrived,
            "note": (
                "A teleport is SUMO removing a vehicle that waited too long. None is "
                "expected here: the demo's threshold outlasts a full cycle."
            ),
        }
        (out_dir / "incident.json").write_text(
            json.dumps(
                {
                    "incident": incident_summary,
                    "queues": queue_summary_data,
                    "policy_transitions": policy_transitions,
                    "signal_timeline": signal_timeline,
                    "run_quality": run_quality,
                },
                indent=2,
            )
        )
        (out_dir / "ambulance.json").write_text(json.dumps(ambulance_summary, indent=2))
    else:
        print(f"  reusing {fcd_file.relative_to(REPO_ROOT)}")
        ambulance_summary = json.loads((out_dir / "ambulance.json").read_text())
        saved = json.loads((out_dir / "incident.json").read_text())
        incident_summary = saved["incident"]
        queue_summary_data = saved["queues"]
        policy_transitions = saved.get("policy_transitions", [])
        signal_timeline = saved.get("signal_timeline", {})
        run_quality = saved.get("run_quality", {})

    print(f"  FCD size: {fcd_file.stat().st_size / 1e6:.1f} MB")

    # ------------------------------------------------------------ scene build
    transform = SceneTransform.from_net_file(net_file)
    layers = LayerIndex.from_geojson(
        REPO_ROOT / "data" / "processed" / args.area / "elevated_and_underground.geojson"
    )
    scene_dir = REPO_ROOT / "frontend" / "public" / "scene"
    if hdemo is not None:
        scene_dir = scene_dir / "historical"
    if args.out:
        scene_dir = scene_dir / args.out
    # Ramped structure ends for HISTORICAL_DEMO only; every other export keeps the
    # flat rule it was validated with.
    elevation_model = (
        ElevationModel(net_file, transform, layers, "ramped") if hdemo is not None else None
    )

    print("  exporting network geometry ...", flush=True)
    network = export_network(net_file, transform, layers, elevation_model=elevation_model)
    network["transform"] = transform.as_dict()
    size = write_json(scene_dir / "network.json", network)
    print(f"    {network['counts']} -> network.json ({size / 1e6:.1f} MB)")

    print("  exporting traffic lights ...", flush=True)
    tls = export_traffic_lights(net_file, transform, layers, elevation_model=elevation_model)
    write_json(scene_dir / "signals.json", {"traffic_lights": tls, "count": len(tls)})
    print(f"    {len(tls)} traffic lights, {sum(t['link_count'] for t in tls)} controlled links")

    print("  exporting buildings ...", flush=True)
    buildings = export_buildings(
        REPO_ROOT / "data" / "raw" / args.area / f"{args.area}.osm.xml",
        transform,
        max_buildings=args.max_buildings,
    )
    if args.historical:
        # An OSM footprint lying on the carriageway is extruded from the ground
        # straight through the road, hiding the traffic and filling the
        # intersection view. See viz.buildings.CARRIAGEWAY_OVERLAP_M.
        kept, overlap = drop_carriageway_overlaps(buildings["buildings"], net_file, transform)
        buildings["buildings"] = kept
        buildings["counts"]["exported"] = len(kept)
        buildings["counts"]["not_drawn_over_carriageway"] = overlap["dropped_count"]
        buildings["carriageway_overlap"] = overlap
        print(f"    {overlap['dropped_count']} footprints sit on the carriageway and are not drawn")
        # What is left can still have a wall mapped a metre into the traffic
        # lane. Trimmed rather than dropped, so the building stays in the scene
        # and only the part standing on the road goes.
        # See viz.buildings.CARRIAGEWAY_CLIP_MARGIN_M.
        kept, clip = clip_to_carriageway(buildings["buildings"], net_file, transform)
        buildings["buildings"] = kept
        buildings["counts"]["exported"] = len(kept)
        buildings["counts"]["trimmed_off_carriageway"] = clip["clipped_count"]
        buildings["carriageway_clip"] = clip
        print(
            f"    {clip['clipped_count']} footprints trimmed off the carriageway "
            f"({clip['area_removed_m2']:.0f} m2 removed, "
            f"{clip['removed_entirely_count']} left nothing to draw)"
        )
    size = write_json(scene_dir / "buildings.json", buildings)
    print(f"    {buildings['counts']} -> buildings.json ({size / 1e6:.1f} MB)")

    print("  exporting trajectories ...", flush=True)
    elevation = lane_elevation_map(net_file, transform, layers)
    window = list(args.window)
    if args.demo or hdemo is not None:
        # The demo's whole point is to end on the arrival. A fixed window that
        # stops before the ambulance gets there would cut the result off screen.
        arrived = ambulance_summary.get("arrived_at_s")
        window = [ambulance.depart_time_s - 60.0, (arrived or args.window[1]) + 12.0]
    trajectories = export_trajectories(
        fcd_file,
        transform,
        begin_s=window[0],
        end_s=window[1],
        lane_elevation=elevation,
        elevation_at=elevation_model.at if elevation_model is not None else None,
    )
    size = write_json(scene_dir / "trajectories.json", trajectories)
    print(f"    {trajectories['counts']} -> trajectories.json ({size / 1e6:.1f} MB)")

    # Embed the paired comparison this scene's policy actually produced, so the
    # viewer shows the research numbers rather than recomputing anything. If the
    # paired run does not exist, the field is absent and the HUD says so - it
    # never shows a zero that could be read as "no benefit measured".
    comparison = None
    if args.demo or hdemo is not None:
        prefix = "hdemo" if hdemo is not None else "demo"
        # DEMO MODE is a different scenario, so a research comparison would be a
        # number from a different experiment shown against this one's traffic.
        # The demo compares itself against the demo NORMAL run of the same seed
        # and demand, or shows nothing.
        if args.policy == "NORMAL":
            comparison = {
                "policy": "NORMAL",
                "is_baseline": True,
                "note": (
                    "HISTORICAL_DEMO baseline. The EMS run is measured against this."
                    if hdemo is not None
                    else "Demo baseline. The EMS run is measured against this."
                ),
                "source": f"{prefix} baseline run",
            }
        else:
            baseline_dir = (
                REPO_ROOT / "simulation" / "results" / f"{config.demand_id}_fcd_{prefix}_NORMAL"
            )
            baseline_file = baseline_dir / "ambulance.json"
            if baseline_file.is_file():
                base = json.loads(baseline_file.read_text())
                saved = (base["travel_time_s"] or 0) - (ambulance_summary["travel_time_s"] or 0)
                comparison = {
                    "policy": args.policy,
                    "baseline_policy": "NORMAL",
                    "baseline_travel_time_s": base["travel_time_s"],
                    "policy_travel_time_s": ambulance_summary["travel_time_s"],
                    "time_saved_s": round(saved, 3),
                    "improvement_percent": round(saved / base["travel_time_s"] * 100, 3)
                    if base["travel_time_s"]
                    else None,
                    "baseline_waiting_s": base["waiting_time_s"],
                    "policy_waiting_s": ambulance_summary["waiting_time_s"],
                    "baseline_stops": base.get("stops"),
                    "policy_stops": ambulance_summary.get("stops"),
                    "source": (
                        "HISTORICAL_DEMO paired NORMAL run: same demand, seed, network and "
                        "trip (NOT the research experiment)"
                        if hdemo is not None
                        else "demo paired run (NOT the research experiment)"
                    ),
                }
        cf_dir = None
        comparison_path = None
    else:
        cf_dir = REPO_ROOT / "data" / "processed" / args.area / "counterfactual"
        comparison_path = cf_dir / (
            f"{'inc_' if incident else ''}{args.trip}_seed{args.seed}_comparisons.json"
        )
    if comparison_path is not None and comparison_path.is_file():
        payload = json.loads(comparison_path.read_text())
        blocks = payload.get("comparisons", {})
        if args.policy in blocks:
            block = blocks[args.policy]
            comparison = {
                "policy": args.policy,
                "baseline_policy": block["baseline_policy"],
                "baseline_travel_time_s": block["ambulance"]["travel_time"]["normal"],
                "policy_travel_time_s": block["ambulance"]["travel_time"]["policy"],
                "time_saved_s": block["ambulance_time_saved_s"],
                "improvement_percent": block["ambulance_improvement_percent"],
                "baseline_waiting_s": block["ambulance"]["waiting_time"]["normal"],
                "policy_waiting_s": block["ambulance"]["waiting_time"]["policy"],
                "baseline_stops": block["ambulance"]["stops"]["normal"],
                "policy_stops": block["ambulance"]["stops"]["policy"],
                "traffic_delta_total_time_loss_s": block["traffic"]["excluding_ambulance"][
                    "deltas"
                ]["total_time_loss_s"]["absolute"],
                "traffic_metric_status": block.get("traffic_metric_status"),
                "source": comparison_path.name,
            }
            # Per-intersection attribution for the same policy, so the scene can
            # show *where* the time went rather than only how much.
            attribution_blocks = payload.get("intersection_attribution", {})
            if args.policy in attribution_blocks:
                comparison["attribution"] = attribution_blocks[args.policy]
        elif args.policy == "NORMAL":
            comparison = {
                "policy": "NORMAL",
                "is_baseline": True,
                "note": (
                    "This run is the baseline the others are measured against, so "
                    "it has no saving of its own."
                ),
                "source": comparison_path.name,
            }

    manifest = {
        "generated_at": utc_now_iso(),
        "sumo_version": installation.version,
        "scenario": {
            "area": args.area,
            "trip": args.trip,
            "seed": args.seed,
            "policy": args.policy,
            "variant": variant.variant_id,
            "demand_id": config.demand_id,
            "window_s": window,
            "fcd_period_s": args.fcd_period,
            "step_length_s": config.step_length_s,
            # What the two paired runs differ by, besides the policy. Recorded so
            # the scene says which SUMO mechanism made the traffic respond, and so
            # the validator can check NORMAL had none of it.
            "sumo_options": (
                demo_run_options(args.policy, ambulance.vehicle_id)
                if hdemo is not None
                else {}
            ),
        },
        # The exact recording this scene was built from. Recorded rather than
        # reconstructed: the validator used to rebuild this path from the demand
        # id and policy, which silently pointed at a different run once the
        # incident exports introduced their own tag.
        "fcd_file": str(fcd_file.relative_to(REPO_ROOT)),
        "mode": "DEMO" if args.demo else "RESEARCH",
        "demo_note": (
            "DEMO MODE: denser demand and a four-point disturbance set, stopped at "
            "the ambulance's arrival. A different scenario from the frozen research "
            "experiment; its numbers are NOT the research result."
            if args.demo
            else None
        ),
        "network_file": str(net_file.relative_to(REPO_ROOT)),
        "network_sha256": sha256_file(net_file),
        "ambulance": ambulance_summary,
        "ambulance_trip_config": ambulance.as_dict(),
        "incident": incident_summary,
        "queues": queue_summary_data,
        "run_quality": run_quality,
        "comparison": comparison,
        "policy_transitions": policy_transitions,
        "signal_timeline": signal_timeline,
        "route_traffic_lights": [t.as_dict() for t in route_tls_for(net_file, route)],
        "transform": transform.as_dict(),
        "provenance": {
            "road_geometry": "PUBLICLY_SOURCED_DATA (OSM via netconvert), unmodified shapes",
            "road_elevation": "ESTIMATED_DATA (OSM layer ordinal x 6 m; network has no z)",
            "kerbs": "ESTIMATED_DATA (generated offset strips; network has no footways)",
            "building_footprints": "PUBLICLY_SOURCED_DATA (OSM building ways)",
            "building_heights": "ESTIMATED_DATA (deterministic rule; 3 of 3,179 ways carry height)",
            "vehicle_positions": "SIMULATED_DATA (SUMO FCD)",
            "signal_states": "SIMULATED_DATA (SUMO TraCI); programs are ESTIMATED_DATA",
            "signal_programs": "ESTIMATED_DATA (netconvert-generated, not observed timings)",
        },
        "not_a_real_world_claim": (
            "This scene renders a simulation. Vehicle positions, signal states and "
            "travel times are SUMO output under estimated demand and generated "
            "signal timings. Nothing in it is a measurement of real traffic or of "
            "any real ambulance journey in Bengaluru."
        ),
    }
    manifest["elevation_model"] = elevation_model.mode if elevation_model is not None else "flat"
    if hdemo is not None:
        apply_historical_manifest(manifest, hdemo, config, demand, tls)
    write_json(scene_dir / "manifest.json", manifest)

    write_record(
        ProvenanceRecord(
            dataset_id=(
                f"hdemo_scene_seed{args.seed}_{args.policy}"
                if hdemo is not None
                else f"phase7_scene_{args.trip}_seed{args.seed}_{args.policy}"
            ),
            data_class=DataClass.SIMULATED,
            description=(
                "Phase 7 3D scene export: SUMO network geometry, traffic-light "
                "layout, OSM building footprints and an FCD trajectory recording, "
                "all transformed into Three.js scene coordinates."
            ),
            produced_by=f"scripts/export_scene.py --seed {args.seed} --trip {args.trip}",
            random_seed=args.seed,
            source_name="EMS Black Box Phase 7",
            retrieved_at=utc_now_iso(),
            file_path=(
                str((scene_dir / "manifest.json").relative_to(REPO_ROOT))
                if hdemo is not None
                else "frontend/public/scene/manifest.json"
            ),
            derived_from=(
                [
                    "historical_central_silk_board",
                    "hdemo_trip_selection",
                    "hdemo_demand_scale_sweep",
                ]
                if hdemo is not None
                else [f"phase5_counterfactual_{args.trip}_seed{args.seed}"]
            ),
            processing_steps=[
                "Ran SUMO with FCD output at the configured period, same seed and policy.",
                "Transformed lane shapes, junctions and signal stop lines to scene metres.",
                "Reconstructed elevation from OSM layer ordinals (the network is 2D).",
                "Transformed FCD samples to scene metres without smoothing or resampling.",
                "Estimated building heights deterministically from footprint area and type.",
            ],
            tool_versions={"sumo": installation.version or "unknown"},
            limitations=[
                "SIMULATED RESULT. Not a measurement of real traffic in Bengaluru.",
                "Road elevation is reconstructed from OSM layer ordinals, not surveyed.",
                "Building heights are overwhelmingly estimated, not sourced.",
                "Kerb strips are generated; the network contains no footway geometry.",
                "Trajectories are clipped to the exported window.",
            ],
        ),
        REPO_ROOT / "data" / "provenance",
    )

    print(f"\nwrote {scene_dir.relative_to(REPO_ROOT)}/")
    print("Simulated scene. Not a measurement of real traffic in Bengaluru.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
