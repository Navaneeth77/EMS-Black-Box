"""The Phase 3 baseline pipeline: demand → routes → simulation → validation.

    DemandConfig ──► flows.xml ──duarouter──► routes.rou.xml
                                                   │
                                              route validation
                                                   │
                                              sumocfg + TraCI run
                                                   │
                                          measurements + validation
                                                   │
                                   demand_report / baseline_validation / provenance

Like Phases 1 and 2, this refuses to hand over a result it cannot vouch for. The
failure being guarded against is a run that completes and reports travel times
that are not the times to drive those journeys — because routes were broken,
because vehicles teleported past the congestion being measured, or because the
demand the configuration asked for never made it onto the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ems_sim.demand.ambulance import AmbulanceTripConfig
from ems_sim.demand.config import BOUNDARY_SINKS, BOUNDARY_SOURCES, DemandConfig
from ems_sim.demand.generator import GeneratedDemand, generate_demand
from ems_sim.demand.route_validation import collect_route_facts, validate_routes
from ems_sim.demand.vehicle_types import VEHICLE_TYPES, vehicle_types_provenance
from ems_sim.network.validation import Severity, ValidationReport
from ems_sim.provenance import (
    DataClass,
    ProvenanceRecord,
    sha256_file,
    utc_now_iso,
    write_record,
)
from ems_sim.runner.measurements import RunMeasurements
from ems_sim.runner.sumo_env import require_sumo
from ems_sim.runner.sumo_process import SumoRunOptions
from ems_sim.runner.traci_bridge import (
    enrich_from_statistics,
    enrich_from_tripinfo,
    run_baseline,
)


class BaselineError(RuntimeError):
    """Raised when a baseline run cannot be produced or trusted."""


# Above this many teleports the run is rejected. A teleport means a vehicle was
# stuck long enough that SUMO moved it, so its recorded travel time is not the
# time to drive that path. A handful in a congested network is tolerable and
# recorded; a stream of them means the demand has gridlocked the network and
# nothing measured on it means anything.
DEFAULT_TELEPORT_THRESHOLD = 10


@dataclass
class BaselineResult:
    """Everything one baseline run produced."""

    demand: GeneratedDemand
    measurements: RunMeasurements
    report: ValidationReport
    sumocfg_file: Path
    output_dir: Path
    written_files: list[Path] = field(default_factory=list)
    provenance_files: list[Path] = field(default_factory=list)
    ambulance_trip: dict[str, Any] = field(default_factory=dict)


def write_sumocfg(
    net_file: Path,
    routes_file: Path,
    config: DemandConfig,
    destination: Path,
    output_dir: Path,
    repo_root: Path,
) -> Path:
    """Write a committed ``.sumocfg`` for the baseline.

    Paths are relative to the config file's own directory, which is what SUMO
    resolves them against — the same trap netconvert configs have.
    """
    import os

    base = destination.parent.resolve()

    def rel(path: Path) -> str:
        return os.path.relpath(path.resolve(), base)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "\n".join(
            [
                "<?xml version='1.0' encoding='UTF-8'?>",
                "",
                "<!--",
                f"  EMS Black Box baseline scenario — {config.demand_id}",
                "",
                f"  period {config.period}  seed {config.seed}",
                f"  demand hash {config.config_hash()}",
                f"  window {config.begin_s:.0f}-{config.end_s:.0f} s "
                f"(warm-up {config.warmup_s:.0f} s)",
                "",
                "  Demand is ESTIMATED_DATA; results are SIMULATED_DATA. Nothing here",
                "  is a measurement of Bengaluru traffic. See docs/TRAFFIC_DEMAND.md",
                "  and docs/BASELINE_SIMULATION.md.",
                "",
                "  Run headlessly with the resolved binary, not the 'sumo' on PATH:",
                f"      $SUMO_HOME/bin/sumo -c {destination.name}",
                "-->",
                "",
                "<configuration>",
                "    <input>",
                f'        <net-file value="{rel(net_file)}"/>',
                f'        <route-files value="{rel(routes_file)}"/>',
                "    </input>",
                "",
                "    <time>",
                f'        <begin value="{config.begin_s}"/>',
                f'        <end value="{config.end_s}"/>',
                f'        <step-length value="{config.step_length_s}"/>',
                "    </time>",
                "",
                "    <processing>",
                f'        <seed value="{config.seed}"/>',
                "        <!-- Teleporting is left at SUMO's default rather than",
                "             disabled: a teleport is a visible symptom of gridlock,",
                "             and switching it off would replace it with vehicles",
                "             jammed forever and travel times that never complete. -->",
                '        <time-to-teleport value="300"/>',
                '        <collision.action value="warn"/>',
                "    </processing>",
                "",
                "    <report>",
                '        <no-step-log value="true"/>',
                '        <duration-log.statistics value="true"/>',
                "    </report>",
                "",
                "    <output>",
                f'        <tripinfo-output value="{rel(output_dir / "tripinfo.xml")}"/>',
                f'        <summary-output value="{rel(output_dir / "summary.xml")}"/>',
                f'        <statistic-output value="{rel(output_dir / "statistics.xml")}"/>',
                f'        <queue-output value="{rel(output_dir / "queues.xml")}"/>',
                f'        <vehroute-output value="{rel(output_dir / "vehroutes.xml")}"/>',
                "    </output>",
                "</configuration>",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def _ambulance_summary(
    measurements: RunMeasurements, ambulance: AmbulanceTripConfig
) -> dict[str, Any]:
    """The ambulance's measured trip, or an explicit statement that it has none.

    A vehicle that did not arrive has no travel time. Substituting the simulation
    end time would produce a number that looks like a measurement and is not one.
    """
    trip = measurements.trips.get(ambulance.vehicle_id)
    if trip is None:
        return {
            "vehicle_id": ambulance.vehicle_id,
            "departed": False,
            "completed": False,
            "travel_time_s": None,
            "note": (
                "The ambulance never entered the network. No travel time exists, "
                "and none has been substituted."
            ),
        }
    completed = trip.arrival_s is not None
    return {
        "vehicle_id": ambulance.vehicle_id,
        "vehicle_type": trip.vehicle_type,
        "origin_edge": ambulance.origin_edge,
        "destination_edge": ambulance.destination_edge,
        "configured_depart_s": ambulance.depart_time_s,
        "departed": True,
        "actual_depart_s": trip.depart_s,
        "completed": completed,
        "arrival_s": trip.arrival_s,
        "travel_time_s": trip.travel_time_s,
        "waiting_time_s": trip.waiting_time_s,
        "time_loss_s": trip.time_loss_s,
        "route_length_m": trip.route_length_m,
        "stop_count": trip.stop_count,
        "teleported": trip.teleported,
        "route_edge_count": len(trip.route_edges),
        "data_class": str(DataClass.SIMULATED),
        "note": (
            "SIMULATED_DATA. The travel time of a simulated ambulance through "
            "simulated traffic, with no priority of any kind. Not a measurement of "
            "any real EMS journey."
            if completed
            else "The ambulance did not arrive before the simulation ended. No "
            "travel time exists, and none has been substituted."
        ),
    }


def validate_baseline(
    measurements: RunMeasurements,
    ambulance: AmbulanceTripConfig,
    config: DemandConfig,
    teleport_threshold: int = DEFAULT_TELEPORT_THRESHOLD,
) -> ValidationReport:
    """Checks on the completed run."""
    report = ValidationReport()

    report.add(
        "simulation_terminated_correctly",
        measurements.sim_end_time_s >= config.end_s - config.step_length_s,
        Severity.ERROR,
        f"Simulation reached {measurements.sim_end_time_s:.1f}s of a configured "
        f"{config.end_s:.1f}s over {measurements.steps_executed} steps",
        measurements.sim_end_time_s,
    )
    report.add(
        "vehicles_departed",
        measurements.departed > 0,
        Severity.ERROR,
        f"{measurements.departed} vehicles departed of {measurements.loaded} loaded",
        measurements.departed,
    )

    backlog = measurements.insertion_backlog_at_end
    report.add(
        "insertion_backlog_is_small",
        measurements.loaded == 0 or backlog / max(measurements.loaded, 1) < 0.10,
        Severity.WARNING,
        f"{backlog} of {measurements.loaded} vehicles were still waiting to be "
        f"inserted at the end. A large backlog means the network could not accept "
        f"the configured demand, so the traffic simulated is lighter than the "
        f"configuration asked for.",
        backlog,
    )

    teleports = len(measurements.teleports)
    report.add(
        "teleports_within_threshold",
        teleports <= teleport_threshold,
        Severity.ERROR,
        (
            f"{teleports} teleport(s), threshold {teleport_threshold}. A teleport "
            f"means a vehicle was stuck long enough that SUMO moved it, so its "
            f"travel time is not the time to drive that path."
            if teleports
            else "No teleports. Every recorded travel time is the time the vehicle "
            "actually spent driving its route."
        ),
        {"observed": teleports, "threshold": teleport_threshold},
    )
    report.add(
        "teleport_observations_match_sumo",
        measurements.sumo_reported_teleports is None
        or measurements.sumo_reported_teleports == teleports,
        Severity.WARNING,
        f"The control loop observed {teleports} teleport(s); SUMO reports "
        f"{measurements.sumo_reported_teleports}. A mismatch means the loop missed "
        f"some, and an unobserved teleport cannot be attributed to a trip.",
        {"observed": teleports, "sumo": measurements.sumo_reported_teleports},
    )
    report.add(
        "no_collisions",
        measurements.collisions == 0,
        Severity.WARNING,
        f"{measurements.collisions} collision(s) reported by SUMO",
        measurements.collisions,
    )

    trip = measurements.trips.get(ambulance.vehicle_id)
    report.add(
        "ambulance_departed",
        trip is not None,
        Severity.ERROR,
        (
            f"The ambulance departed at {trip.depart_s:.1f}s"
            if trip
            else "The ambulance never entered the network"
        ),
        trip is not None,
    )
    completed = trip is not None and trip.arrival_s is not None
    report.add(
        "ambulance_completed_its_route",
        completed,
        Severity.ERROR,
        (
            f"The ambulance completed its route in {trip.travel_time_s:.1f}s "
            f"({trip.waiting_time_s:.1f}s waiting, {trip.time_loss_s:.1f}s time loss)"
            if completed
            else "The ambulance did not arrive before the simulation ended. Extend "
            "the run or move the departure earlier; no travel time has been "
            "substituted."
        ),
        completed,
    )
    if trip is not None:
        report.add(
            "ambulance_was_not_teleported",
            not trip.teleported,
            Severity.ERROR,
            (
                "The ambulance was teleported, so its travel time is not the time "
                "to drive its route and must not be reported"
                if trip.teleported
                else "The ambulance drove its whole route without being teleported."
            ),
            trip.teleported,
        )

    completed_trips = [t for t in measurements.trips.values() if t.arrival_s is not None]
    report.add(
        "background_trips_completed",
        len(completed_trips) > 0,
        Severity.ERROR,
        f"{len(completed_trips)} of {len(measurements.trips)} trips completed. "
        f"Vehicles still running at the end are expected: demand runs to the end of "
        f"the window, so the last departures cannot finish.",
        len(completed_trips),
    )
    return report


def default_provenance_report(net_file: Path, review_file: Path | None) -> dict[str, Any]:
    """Expose the Phase 2.5 lane and speed provenance to the demand layer.

    Phase 3 does **not** replace any default. This surfaces what is OSM-derived
    and what SUMO supplied, so a later phase can filter on it and so no figure
    computed over this network is quoted without the caveat.

    The word "real" is not used for a SUMO-supplied value anywhere in this report.
    """
    from ems_sim.network.sumo_review import review_defaults

    defaults = review_defaults(net_file)
    payload = {
        "osm_derived": {
            "description": (
                "Lane counts and speeds present in OpenStreetMap and carried through "
                "the conversion unchanged. PUBLICLY_SOURCED_DATA: community-mapped "
                "and not independently verified by this project."
            ),
            "data_class": str(DataClass.PUBLICLY_SOURCED),
            "edges_with_osm_lane_count": defaults["total_edges"] - defaults["lanes_defaulted"],
            "edges_with_osm_speed": defaults["total_edges"] - defaults["speed_defaulted"],
            "edges_fully_osm_sourced": defaults["edges_fully_osm_sourced"],
            "speed_histogram_kmh": defaults["speed_histogram_kmh"]["from_osm"],
            "lane_histogram": defaults["lane_histogram"]["from_osm"],
        },
        "sumo_estimated": {
            "description": (
                "Lane counts and speeds netconvert supplied from its built-in type "
                "map because OpenStreetMap had none. SUMO cannot simulate an edge "
                "without both, so the substitution is unavoidable. These are "
                "ESTIMATED_DATA and are not properties of the real road."
            ),
            "data_class": str(DataClass.ESTIMATED),
            "edges_with_estimated_lane_count": defaults["lanes_defaulted"],
            "edges_with_estimated_speed": defaults["speed_defaulted"],
            "speed_histogram_kmh": defaults["speed_histogram_kmh"]["defaulted"],
            "lane_histogram": defaults["lane_histogram"]["defaulted"],
            "implausible_default_speeds": defaults["implausible_default_speeds"]["count"],
            "implausible_threshold_kmh": defaults["implausible_default_speeds"]["threshold_kmh"],
            "edge_ids": defaults["defaulted_edge_ids"],
        },
        "by_road_class": defaults["by_road_class"],
        "consequence_for_this_phase": (
            "Free-flow speed is the reference that time loss and delay are measured "
            "against, and SUMO supplied it on "
            f"{defaults['speed_defaulted']} of {defaults['total_edges']} edges — "
            f"{defaults['implausible_default_speeds']['count']} of them at or above "
            f"{defaults['implausible_default_speeds']['threshold_kmh']:.0f} km/h. Time "
            "loss reported by this baseline is therefore an over-estimate on those "
            "edges. Not corrected here: no source in this project supports a better "
            "value, and substituting a guess would be worse than a labelled default."
        ),
    }
    if review_file is not None and review_file.is_file():
        payload["phase2_5_review"] = str(review_file)
    return payload


def run_phase3_baseline(
    config: DemandConfig,
    ambulance: AmbulanceTripConfig,
    repo_root: Path,
    area_id: str = "silk_board_v1",
    teleport_threshold: int = DEFAULT_TELEPORT_THRESHOLD,
    allow_validation_errors: bool = False,
    sample_interval_s: float = 10.0,
) -> BaselineResult:
    """Generate demand, validate routes, simulate, validate results, record."""
    installation = require_sumo()
    net_file = repo_root / "simulation" / "sumo" / area_id / f"{area_id}.net.xml"
    if not net_file.is_file():
        raise BaselineError(
            f"No SUMO network at {net_file.relative_to(repo_root)}. "
            "Run scripts/build_sumo_network.py first."
        )

    processed_dir = repo_root / "data" / "processed" / area_id
    output_dir = repo_root / "simulation" / "results" / config.demand_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. demand and routes ---------------------------------------------
    demand = generate_demand(config, ambulance, net_file, repo_root, installation)

    from ems_sim.network.sumo_pipeline import load_phase1_layers

    phase1_layers, _ = load_phase1_layers(processed_dir)
    route_facts = collect_route_facts(
        demand.routes_file,
        net_file,
        phase1_layers,
        config.begin_s,
        config.end_s,
        ambulance.vehicle_id,
        installation,
    )
    report = validate_routes(route_facts, BOUNDARY_SOURCES, BOUNDARY_SINKS)

    if not report.ok and not allow_validation_errors:
        failures = "\n  ".join(f"{c.name}: {c.detail}" for c in report.errors)
        raise BaselineError(
            f"Route validation failed with {len(report.errors)} error(s). Nothing "
            f"was simulated: a run over broken routes produces travel times that "
            f"are not the times to drive those journeys.\n  {failures}"
        )

    # --- 2. simulate --------------------------------------------------------
    sumocfg = write_sumocfg(
        net_file,
        demand.routes_file,
        config,
        repo_root / "simulation" / "config" / f"{config.demand_id}.sumocfg",
        output_dir,
        repo_root,
    )
    options = SumoRunOptions(
        net_file=net_file,
        route_files=(demand.routes_file,),
        begin_s=config.begin_s,
        end_s=config.end_s,
        step_length_s=config.step_length_s,
        seed=config.seed,
        tripinfo_output=output_dir / "tripinfo.xml",
        summary_output=output_dir / "summary.xml",
        statistic_output=output_dir / "statistics.xml",
        queue_output=output_dir / "queues.xml",
        vehroute_output=output_dir / "vehroutes.xml",
    )
    measurements = run_baseline(
        options, ambulance.vehicle_id, installation, sample_interval_s=sample_interval_s
    )
    enrich_from_statistics(measurements, output_dir / "statistics.xml")
    enrich_from_tripinfo(measurements, output_dir / "tripinfo.xml")

    for name, passed, severity, detail, value in _as_tuples(
        validate_baseline(measurements, ambulance, config, teleport_threshold)
    ):
        report.add(name, passed, severity, detail, value)

    ambulance_trip = _ambulance_summary(measurements, ambulance)

    # --- 3. reports ---------------------------------------------------------
    written = _write_reports(
        repo_root,
        processed_dir,
        output_dir,
        config,
        ambulance,
        demand,
        route_facts,
        measurements,
        report,
        ambulance_trip,
        net_file,
        installation.version or "unknown",
    )
    provenance_files = [
        _write_provenance(
            repo_root,
            config,
            ambulance,
            demand,
            measurements,
            report,
            ambulance_trip,
            net_file,
            sumocfg,
            installation.version or "unknown",
        )
    ]

    if not report.ok and not allow_validation_errors:
        failures = "\n  ".join(f"{c.name}: {c.detail}" for c in report.errors)
        raise BaselineError(
            f"Baseline validation failed with {len(report.errors)} error(s). Reports "
            f"were written so the failure can be inspected, but the results must not "
            f"be used.\n  {failures}"
        )

    return BaselineResult(
        demand=demand,
        measurements=measurements,
        report=report,
        sumocfg_file=sumocfg,
        output_dir=output_dir,
        written_files=written,
        provenance_files=provenance_files,
        ambulance_trip=ambulance_trip,
    )


def _as_tuples(report: ValidationReport):
    for check in report.checks:
        yield check.name, check.passed, check.severity, check.detail, check.value


def _json_default(obj: object) -> object:
    item = getattr(obj, "item", None)
    if callable(item):
        return item()
    if isinstance(obj, set | frozenset):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8")
    return path


def _reproducibility(
    config: DemandConfig,
    ambulance: AmbulanceTripConfig,
    net_file: Path,
    sumo_version: str,
) -> dict[str, Any]:
    """Everything needed to regenerate this run."""
    return {
        "generated_at": utc_now_iso(),
        "sumo_version": sumo_version,
        "network_file": str(net_file),
        "network_sha256": sha256_file(net_file),
        "demand_config_hash": config.config_hash(),
        "seed": config.seed,
        "step_length_s": config.step_length_s,
        "begin_s": config.begin_s,
        "end_s": config.end_s,
        "warmup_s": config.warmup_s,
        "period": str(config.period),
        "ambulance": ambulance.as_dict(),
        "vehicle_type_count": len(VEHICLE_TYPES),
    }


def _write_reports(
    repo_root: Path,
    processed_dir: Path,
    output_dir: Path,
    config: DemandConfig,
    ambulance: AmbulanceTripConfig,
    demand: GeneratedDemand,
    route_facts,
    measurements: RunMeasurements,
    report: ValidationReport,
    ambulance_trip: dict[str, Any],
    net_file: Path,
    sumo_version: str,
) -> list[Path]:
    written: list[Path] = []
    reproducibility = _reproducibility(config, ambulance, net_file, sumo_version)
    review_file = processed_dir / "network_review.json"

    written.append(
        _write_json(
            processed_dir / "demand_report.json",
            {
                "reproducibility": reproducibility,
                "demand_configuration": config.as_dict(),
                "vehicle_types": vehicle_types_provenance(),
                "ambulance": ambulance.as_dict(),
                "generated": demand.as_dict(),
                "routes": route_facts.as_dict(),
                "boundary": {
                    "sources": BOUNDARY_SOURCES,
                    "sinks": BOUNDARY_SINKS,
                    "note": (
                        "The network is a 1.6 km box cut out of Bengaluru. Traffic "
                        "enters at edges with no incoming edge inside the box and "
                        "leaves at edges with no outgoing edge. Vehicles are not "
                        "created or destroyed anywhere else."
                    ),
                },
                "lane_and_speed_provenance": default_provenance_report(net_file, review_file),
            },
        )
    )
    written.append(
        _write_json(
            processed_dir / "baseline_validation.json",
            {
                "reproducibility": reproducibility,
                "simulation": measurements.as_dict(),
                "ambulance": ambulance_trip,
                "validation": report.as_dict(),
                "teleport_events": [t.as_dict() for t in measurements.teleports],
            },
        )
    )
    written.append(
        _write_json(
            output_dir / "measurements.json",
            {
                "reproducibility": reproducibility,
                "simulation": measurements.as_dict(),
                "ambulance": ambulance_trip,
                "samples": [
                    {
                        "sim_time_s": s.sim_time_s,
                        "running_vehicles": s.running_vehicles,
                        "halting_vehicles": s.halting_vehicles,
                        "mean_speed_ms": s.mean_speed_ms,
                        "teleports_cumulative": s.teleports_cumulative,
                        "ambulance_speed_ms": s.ambulance_speed_ms,
                        "ambulance_edge": s.ambulance_edge,
                        "ambulance_waiting_s": s.ambulance_waiting_s,
                    }
                    for s in measurements.samples
                ],
                "data_class": str(DataClass.SIMULATED),
            },
        )
    )
    return written


def _write_provenance(
    repo_root: Path,
    config: DemandConfig,
    ambulance: AmbulanceTripConfig,
    demand: GeneratedDemand,
    measurements: RunMeasurements,
    report: ValidationReport,
    ambulance_trip: dict[str, Any],
    net_file: Path,
    sumocfg: Path,
    sumo_version: str,
) -> Path:
    record = ProvenanceRecord(
        dataset_id="phase3_baseline",
        data_class=DataClass.SIMULATED,
        description=(
            f"Phase 3 baseline traffic simulation for silk_board_v1, period "
            f"{config.period}, seed {config.seed}. Vehicle movements, travel times, "
            f"waiting times and time loss produced by SUMO from an assumed demand "
            f"model over the OpenStreetMap-derived network."
        ),
        produced_by=f"demand:{config.config_hash()} net:{sha256_file(net_file)[:16]}",
        random_seed=config.seed,
        source_name="EMS Black Box Phase 3 baseline",
        retrieved_at=utc_now_iso(),
        file_path=str(
            (
                repo_root / "data" / "processed" / "silk_board_v1" / "baseline_validation.json"
            ).relative_to(repo_root)
        ),
        derived_from=[
            "silk_board_v1_osm_raw",
            "silk_board_v1_network_processed",
            "silk_board_v1_sumo_network",
            "sumo_network_review",
        ],
        processing_steps=[
            f"Wrote flow definitions from the committed demand configuration "
            f"({len(config.flows)} OD flows, {len(config.vehicle_mix)} vehicle types).",
            "Routed with duarouter using the recorded seed, without --ignore-errors "
            "or --repair: an unroutable OD pair is a finding, not something to skip.",
            "Validated routes: connectivity, grade transitions, boundary entry and "
            "exit, and use of infrastructure whose status Phase 2.5 left unresolved.",
            f"Simulated under TraCI control with SUMO {sumo_version} at "
            f"{config.step_length_s}s steps to {config.end_s:.0f}s.",
            "Recorded teleports per vehicle and cross-checked the count against SUMO's own tally.",
            "Took per-trip figures from SUMO's tripinfo output and totals from its "
            "statistics output rather than reconstructing them.",
        ],
        tool_versions={"sumo": sumo_version},
        limitations=[
            "THE DEMAND IS NOT MEASURED BENGALURU TRAFFIC. Flow rates, vehicle mix "
            "and time-of-day scaling are ESTIMATED_DATA chosen by this project. "
            "Absolute travel times describe this modelled traffic, not the real "
            "junction. They are a baseline to subtract a counterfactual from.",
            "Vehicle-type parameters are ESTIMATED_DATA. None was measured at Silk "
            "Board. Two-wheeler lane-filtering in particular is modelled by an "
            "uncalibrated lcAssertive value and drives saturation flow.",
            "The ambulance origin, destination and departure time are configuration "
            "choices, not a real EMS dispatch record.",
            "Traffic is boundary-to-boundary only. Trips beginning or ending inside "
            "the study area are not modelled, so the mix is skewed toward "
            "through-traffic.",
            "Traffic-light programs are netconvert defaults and remain "
            "ESTIMATED_DATA. No real Bengaluru signal timing exists in this project.",
            "Free-flow speed is SUMO-supplied on most edges, so reported time loss "
            "is an over-estimate where the default exceeds the real limit. See the "
            "lane_and_speed_provenance section of demand_report.json.",
            *report.limitations(),
        ],
        notes=(
            f"{measurements.departed} vehicles departed, {measurements.arrived} "
            f"arrived, {len(measurements.teleports)} teleports. Ambulance completed: "
            f"{ambulance_trip.get('completed')}. Config: {sumocfg.relative_to(repo_root)}."
        ),
    )
    return write_record(record, repo_root / "data" / "provenance")
