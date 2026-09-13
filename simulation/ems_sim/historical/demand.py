"""Historical observed Silk Board volume to SUMO demand, with every step labelled.

The pipeline, and the label each step carries:

1. **OBSERVED** — the junction's peak-hour volume, 22,634 vehicles per hour
   (CMP 2019 draft, Table 2-13, from the RMP-2031 counts of Dec 2014-Apr 2015).
   Vehicles, not PCU; peak hour, not 24 hours.
2. **DERIVED** — ratios of observed values from that same table (peak-hour share
   of the day, PCU per vehicle). Reported as context; none drives the demand.
3. **ESTIMATED** — the demand scale ``k`` and the ambulance's departure. The
   modelled 1.6 km network cannot carry the observed hour: the research found its
   own, much lighter demand gridlocked at scale 1.0. SUMO is therefore given
   ``22,634 x k`` vehicles per hour. ``k`` and the departure are chosen together by
   ``scripts/historical_scenario_sweep.py``: the least demand and earliest departure
   on grids fixed in advance for which the NORMAL run stays valid (no teleports,
   small insertion backlog, ambulance arrives) **and** the ambulance is caught in a
   queue rather than leading it. Every run in that search is a NORMAL run; the rule
   never looks at an EMS result.
4. **ESTIMATED** — how that total is spread over origins and destinations. No
   turning movement count for Silk Board was found, so the committed research OD
   structure supplies the relative weights, unchanged.
5. **OBSERVED share, preserved exactly** — cars are 53% of the flow rate of every
   OD pair. The remaining 47% is split across the other SUMO vehicle types in the
   proportions of the research model's ESTIMATED mix, because no observed breakdown
   of non-car traffic at Silk Board exists. That split is labelled ESTIMATED and
   is never presented as observed.
6. **SIMULATED** — every vehicle SUMO then inserts, moves and queues.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ems_sim.demand.ambulance import AmbulanceTripConfig
from ems_sim.demand.config import (
    BASELINE_FLOWS,
    VEHICLE_MIX,
    DemandConfig,
    DemandPeriod,
    make_config,
)
from ems_sim.demand.generator import (
    DemandGenerationError,
    GeneratedDemand,
    run_duarouter,
    summarise_routes,
)
from ems_sim.demand.vehicle_types import vehicle_types_xml
from ems_sim.historical.sources import HistoricalObservations
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo

NON_CAR_TYPES: tuple[str, ...] = ("motorcycle", "auto", "bus", "van", "truck")

SCALE_GRID: tuple[float, ...] = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
"""ESTIMATED search grid for ``k``. Every value is a fraction of the observed peak
hour, so a larger one is closer to what was counted, not further from it. The grid
was extended upward from 0.25 when the demo needed a queue the ambulance could get
caught in; the validity rule it is searched under did not change."""

DEPART_GRID_S: tuple[float, ...] = (600.0, 700.0, 800.0, 900.0, 1000.0)
"""Candidate ambulance departure times, on a 100 s grid from the project's 600 s
convention. Which one is used is decided by the scenario rule in
``scripts/historical_scenario_sweep.py`` from NORMAL runs only."""

MIN_VEHICLES_AHEAD = 12
"""How much traffic must be between the ambulance and the stop line when it first
halts on the four-way approach, for the demo to show an ambulance caught in a queue
rather than one leading it. Twelve is about four vehicles in each of the approach's
three lanes. A scenario requirement, not a result."""

QUEUE_HALT_WINDOW_M = 400.0
"""How close to the four-way's stop line a halt has to be to count as joining its
queue. Longer than any queue this demo produces, short enough to exclude halts at
the other signals on the route."""

MIN_QUEUE_HALT_DISTANCE_M = 25.0
"""And it must not halt on the stop line itself. This is deliberately a weak test:
the four-way's approach edge is only about 58 m long, so a larger distance would
demand that the queue spill past the junction upstream — an accident of this
geometry rather than a property of being stuck in a queue. The vehicle count above
is what carries the requirement."""

TIME_TO_TELEPORT_S = 600.0
"""ESTIMATED. SUMO's 300 s default would remove the head of a queue before the
observed 450 s cycle could serve it: the longest red in the split-phase program is
342.5 s. 600 s is one full observed cycle plus 150 s, so a vehicle waiting through
a complete red is not teleported, while a true gridlock still is."""

LATERAL_RESOLUTION_M = 0.8
"""Sublane resolution for the HISTORICAL_DEMO runs. **Both** of them.

Without it a SUMO lane is one-dimensional: a vehicle is in a lane or it is not,
and there is no such thing as edging over. That is fine for measuring flow, and
it is what the frozen research runs use. It is not fine for showing traffic
getting out of an ambulance's way, and it does not merely look worse — it
deadlocks. Measured, in the run that made this necessary: with the siren on and
lanes indivisible, the vehicles in front stopped to let the ambulance through,
had nowhere to stop *to*, and the ambulance stood at the same metre for 3,094 s
until the simulation ended.

At 0.8 m the lane is divided into four strips, so a car can sit to one side of
its lane and the vehicles beside it can do the same, leaving a gap along the lane
boundary that a 2.2 m ambulance can use. That is SUMO's rescue-lane behaviour and
it is what the bluelight device is designed to work with.

It is a **model setting, not an intervention**: both paired runs use it, so the
NORMAL baseline is subject to exactly the same physics. The research runs are
untouched.
"""

MAX_BACKLOG_FRACTION = 0.05
"""ESTIMATED validity bound: vehicles still waiting to enter at the end of the run,
as a fraction of vehicles that entered."""


def base_od_total_vph() -> float:
    """Sum of the committed research OD rates before any scaling."""
    return float(sum(flow.vehicles_per_hour for flow in BASELINE_FLOWS))


def derived_quantities(obs: HistoricalObservations) -> list[dict[str, Any]]:
    """Ratios of observed values from the same table. Context only."""
    return [
        {
            "id": "peak_hour_share_of_daily_vehicles",
            "value": round(obs.peak_hour_vehicles / obs.daily_vehicles, 6),
            "unit": "fraction",
            "formula": "peak_hour_volume_vehicles / daily_volume_vehicles",
            "calculation": f"{obs.peak_hour_vehicles:.0f} / {obs.daily_vehicles:.0f}",
            "label": "DERIVED",
            "used_for_demand": False,
        },
        {
            "id": "pcu_per_vehicle_peak_hour",
            "value": round(obs.peak_hour_pcu / obs.peak_hour_vehicles, 6),
            "unit": "PCU per vehicle",
            "formula": "peak_hour_volume_pcu / peak_hour_volume_vehicles",
            "calculation": f"{obs.peak_hour_pcu:.0f} / {obs.peak_hour_vehicles:.0f}",
            "label": "DERIVED",
            "used_for_demand": False,
            "note": "The PCU factors the survey used are not reported, so this ratio "
            "cannot be inverted into a vehicle-class split.",
        },
        {
            "id": "pcu_per_vehicle_daily",
            "value": round(obs.daily_pcu / obs.daily_vehicles, 6),
            "unit": "PCU per vehicle",
            "formula": "daily_volume_pcu / daily_volume_vehicles",
            "calculation": f"{obs.daily_pcu:.0f} / {obs.daily_vehicles:.0f}",
            "label": "DERIVED",
            "used_for_demand": False,
        },
        {
            "id": "mean_hourly_vehicles_over_24h",
            "value": round(obs.daily_vehicles / 24.0, 2),
            "unit": "vehicles per hour",
            "formula": "daily_volume_vehicles / 24",
            "calculation": f"{obs.daily_vehicles:.0f} / 24",
            "label": "DERIVED",
            "used_for_demand": False,
        },
    ]


def historical_vehicle_mix(obs: HistoricalObservations) -> dict[str, float]:
    """Car share OBSERVED and preserved exactly; the non-car split ESTIMATED."""
    car = obs.car_share
    base = {type_id: VEHICLE_MIX[type_id] for type_id in NON_CAR_TYPES}
    total = sum(base.values())
    mix = {"car": car}
    for type_id, share in base.items():
        mix[type_id] = (1.0 - car) * share / total
    return mix


def demand_scale_for(obs: HistoricalObservations, k: float) -> float:
    """Multiplier on the research OD rates that makes their total ``observed x k``."""
    return obs.peak_hour_vehicles * k / base_od_total_vph()


def demand_id_for(k: float, seed: int, depart_time_s: float) -> str:
    """One id per scenario. The departure is part of it because the routes file
    carries the ambulance's trip: two departures are two different demand sets."""
    return f"hdemo_silk_board_v1_seed{seed}_k{k:g}_d{depart_time_s:.0f}"


def historical_demand_config(
    obs: HistoricalObservations,
    k: float,
    seed: int = 42,
    depart_time_s: float = 600.0,
    duration_s: float = 3600.0,
    warmup_s: float = 300.0,
) -> DemandConfig:
    """The research OD structure, re-weighted to the observed volume and car share.

    The period is recorded as evening peak only because the config type needs one;
    both peaks have period scaling 1.0, and the observed quantity is already a
    peak-hour volume, so the choice changes no flow.
    """
    config = make_config(
        period=DemandPeriod.EVENING_PEAK,
        seed=seed,
        duration_s=duration_s,
        warmup_s=warmup_s,
        demand_scale=demand_scale_for(obs, k),
    )
    return replace(
        config,
        demand_id=demand_id_for(k, seed, depart_time_s),
        vehicle_mix=historical_vehicle_mix(obs),
    )


def conversion_record(
    obs: HistoricalObservations,
    k: float,
    config: DemandConfig,
    generated: GeneratedDemand | None = None,
    scale_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole OBSERVED -> SUMO chain as one auditable, labelled record."""
    scaled_total = sum(f.vehicles_per_hour for f in config.scaled_flows())
    mix = config.vehicle_mix
    by_od = [
        {
            "from_edge": flow.from_edge,
            "to_edge": flow.to_edge,
            "label": flow.label,
            "research_weight_vph": base.vehicles_per_hour,
            "sumo_vph": flow.vehicles_per_hour,
            "sumo_vph_by_type": {t: round(flow.vehicles_per_hour * s, 4) for t, s in mix.items()},
            "data_class": "ESTIMATED",
        }
        for flow, base in zip(config.scaled_flows(), BASELINE_FLOWS, strict=True)
    ]
    record: dict[str, Any] = {
        "mode": "HISTORICAL_DEMO",
        "demand_id": config.demand_id,
        "seed": config.seed,
        "config_hash": config.config_hash(),
        "pipeline": [
            {
                "step": 1,
                "label": "OBSERVED",
                "what": "Silk Board peak-hour volume",
                "value": obs.peak_hour_vehicles,
                "unit": "vehicles per hour",
                "source": obs.get("peak_hour_volume_vehicles", "vehicles per hour").as_dict(),
                "not_used_in_its_place": [
                    "peak_hour_volume_pcu (PCU per hour)",
                    "daily_volume_vehicles (vehicles per 24 hours)",
                    "daily_volume_pcu (PCU per 24 hours)",
                ],
            },
            {
                "step": 2,
                "label": "DERIVED",
                "what": "Ratios from the same table, reported as context only",
                "values": derived_quantities(obs),
            },
            {
                "step": 3,
                "label": "ESTIMATED",
                "what": "Demand scale k: share of the observed hour given to SUMO",
                "k": k,
                "calculation": f"{obs.peak_hour_vehicles:.0f} veh/h x {k:g} = "
                f"{obs.peak_hour_vehicles * k:.1f} veh/h",
                "target_sumo_input_vph": round(obs.peak_hour_vehicles * k, 3),
                "sumo_input_vph_after_rounding": round(scaled_total, 3),
                "why": "The modelled network cannot carry the observed hour. k and the "
                "ambulance's departure are chosen together, on grids fixed in advance: the "
                "most traffic the network carries validly, and the earliest departure at "
                "which the NORMAL ambulance is caught in the four-way's queue rather than "
                "leading it. Every run in that search is a NORMAL run; the rule never uses "
                "an EMS result.",
                "selection": scale_selection,
            },
            {
                "step": 4,
                "label": "ESTIMATED",
                "what": "Origin-destination spread",
                "basis": "No Silk Board turning movement count was found. The research "
                "model's committed OD pairs keep their relative weights and are scaled "
                "so they total the step-3 input.",
                "research_od_total_vph": base_od_total_vph(),
                "multiplier_on_research_od": round(config.demand_scale, 6),
                "flows": by_od,
            },
            {
                "step": 5,
                "label": "OBSERVED share preserved; remainder ESTIMATED",
                "what": "Vehicle composition applied to every OD flow",
                "car_share": {"value": mix["car"], "label": "OBSERVED", "source_id": "IJIRSET2017"},
                "non_car_split": {
                    "values": {t: round(mix[t], 6) for t in NON_CAR_TYPES},
                    "label": "ESTIMATED",
                    "basis": "The 47% that is not cars is split in the proportions of "
                    "the research model's ESTIMATED mix (motorcycle 0.50, auto 0.14, "
                    "bus 0.03, van 0.04, truck 0.03). No observed breakdown exists.",
                },
                "cross_source_caveat": "The car share and the volume come from different "
                "surveys (IJIRSET 2017; RMP-2031 counts of Dec 2014-Apr 2015). Applying "
                "one to the other is ESTIMATED.",
                "estimated_cars_in_observed_peak_hour": {
                    "value": round(obs.peak_hour_vehicles * mix["car"], 2),
                    "calculation": f"{obs.peak_hour_vehicles:.0f} x {mix['car']:g}",
                    "label": "ESTIMATED",
                    "why_not_derived": "cross-source: share and volume are from different "
                    "surveys",
                },
            },
            {
                "step": 6,
                "label": "SIMULATED",
                "what": "Vehicles routed by duarouter and simulated by SUMO",
                "routed_vehicle_count": generated.vehicle_count if generated else None,
                "routed_vehicles_by_type": generated.vehicles_by_type if generated else None,
            },
        ],
        "sumo_parameters": {
            "time_to_teleport_s": {
                "value": TIME_TO_TELEPORT_S,
                "label": "ESTIMATED",
                "basis": "One observed 450 s cycle plus 150 s; see demand.TIME_TO_TELEPORT_S.",
            }
        },
        "not_claimed": [
            "SUMO vehicle positions are not historical GPS traces.",
            "Queues and travel times are simulated, not observed.",
            "Turning proportions are not observed.",
            "The non-car vehicle split is not observed.",
        ],
    }
    return record


def write_historical_flows(
    config: DemandConfig,
    ambulance: AmbulanceTripConfig,
    destination: Path,
    k: float,
    obs: HistoricalObservations,
) -> Path:
    """Flow definitions for one HISTORICAL_DEMO demand set."""
    lines: list[str] = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        "",
        "<!--",
        f"  EMS Black Box HISTORICAL_DEMO demand - {config.demand_id}",
        f"  seed {config.seed}  hash {config.config_hash()}",
        "",
        f"  Total input {obs.peak_hour_vehicles:.0f} veh/h (OBSERVED peak hour, Silk Board,",
        "  CMP 2019 draft Table 2-13) multiplied by k = "
        f"{k:g} (ESTIMATED; see demand_conversion.json).",
        "  Cars are 53% of every flow (OBSERVED share, IJIRSET 2017). The OD spread and",
        "  the non-car split are ESTIMATED. Vehicles simulated from this file are",
        "  SIMULATED_DATA, not historical GPS traces.",
        "",
        "  Generated by ems_sim.historical.demand. Do not edit by hand.",
        "-->",
        "",
        "<routes xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'"
        " xsi:noNamespaceSchemaLocation='http://sumo.dlr.de/xsd/routes_file.xsd'>",
        "",
        vehicle_types_xml(),
        "",
    ]
    index = 0
    for flow in config.scaled_flows():
        for type_id, share in sorted(config.vehicle_mix.items()):
            rate = flow.vehicles_per_hour * share
            if rate <= 0:
                continue
            lines.append(
                f'    <flow id="h{index:03d}_{type_id}" type="{type_id}" '
                f'begin="{config.begin_s:.1f}" end="{config.end_s:.1f}" '
                f'from="{flow.from_edge}" to="{flow.to_edge}" '
                f'vehsPerHour="{rate:.4f}" departLane="best" departSpeed="max"/>'
            )
            index += 1
        lines.append(f"    <!-- {flow.label}: {flow.vehicles_per_hour:.1f} veh/h total -->")
    lines += [
        "",
        f'    <trip id="{ambulance.vehicle_id}" type="{ambulance.vehicle_type}" '
        f'depart="{ambulance.depart_time_s:.1f}" '
        f'from="{ambulance.origin_edge}" to="{ambulance.destination_edge}" '
        f'departLane="best" departSpeed="max"/>',
        "",
        "</routes>",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def generate_historical_demand(
    obs: HistoricalObservations,
    k: float,
    ambulance: AmbulanceTripConfig,
    net_file: Path,
    repo_root: Path,
    seed: int = 42,
    installation: SumoInstallation | None = None,
) -> GeneratedDemand:
    """Write flows, route them with duarouter (no --ignore-errors), summarise."""
    installation = installation or require_sumo()
    config = historical_demand_config(
        obs, k, seed=seed, depart_time_s=ambulance.depart_time_s
    )
    flows_file = repo_root / "simulation" / "demand" / f"{config.demand_id}.flows.xml"
    routes_file = repo_root / "simulation" / "routes" / f"{config.demand_id}.rou.xml"
    write_historical_flows(config, ambulance, flows_file, k, obs)
    command, warnings = run_duarouter(
        net_file, flows_file, routes_file, config, repo_root, installation
    )
    total, by_type, ambulance_edges = summarise_routes(routes_file, ambulance)
    if not ambulance_edges:
        raise DemandGenerationError(
            f"duarouter produced no route for {ambulance.vehicle_id}; the HISTORICAL_DEMO "
            f"trip cannot be simulated."
        )
    return GeneratedDemand(
        flows_file=flows_file,
        routes_file=routes_file,
        config=config,
        ambulance=ambulance,
        duarouter_command=command,
        duarouter_warnings=warnings,
        vehicle_count=total,
        vehicles_by_type=by_type,
        ambulance_route_edges=ambulance_edges,
    )
