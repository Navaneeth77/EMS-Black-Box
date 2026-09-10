"""Run one scenario under one signal policy, under TraCI control.

The loop is the Phase 3 baseline loop with a policy hook and three additions
that the counterfactual claim depends on:

* **The ambulance is observed, never moved.** Its edge, lane, position and speed
  are read from SUMO each step and handed to the policy. Nothing writes to it.
  A policy that could nudge the ambulance would make "time saved" measure the
  nudge.
* **Every signal state is validated against the program.** Policies only select
  among netconvert's own phases, so a state string that is not one of them means
  something wrote a signal directly — and could have created conflicting greens.
  Checked every step rather than assumed.
* **Teleports invalidate the run for headline use.** Ambulance travel time is the
  headline metric; a teleported ambulance did not drive the distance it is
  credited with. Such a run is flagged, not silently averaged in.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ems_sim.policies.base import AmbulanceObservation, BasePolicy
from ems_sim.policies.tls_map import RouteTls, load_programs, traffic_lights_on_route
from ems_sim.runner.measurements import RunMeasurements, TeleportEvent, VehicleTrip
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo
from ems_sim.runner.sumo_process import (
    SumoRunOptions,
    build_sumo_command,
    free_port,
    sumo_environment,
)


@dataclass
class SignalConflict:
    """An observed signal state that is not one the program defines."""

    sim_time_s: float
    tls_id: str
    observed_state: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sim_time_s": self.sim_time_s,
            "tls_id": self.tls_id,
            "observed_state": self.observed_state,
            "reason": self.reason,
        }


@dataclass
class PolicyRunResult:
    """One policy run over one scenario."""

    policy: str
    seed: int
    measurements: RunMeasurements
    policy_report: dict[str, Any]
    route_tls: list[dict[str, Any]] = field(default_factory=list)
    signal_conflicts: list[SignalConflict] = field(default_factory=list)
    ambulance_route_edges: list[str] = field(default_factory=list)
    ambulance_edge_times: dict[str, float] = field(default_factory=dict)
    ambulance_signal_waits: list[dict[str, Any]] = field(default_factory=list)
    ambulance_stop_count: int = 0
    output_dir: str = ""

    @property
    def ambulance(self) -> VehicleTrip | None:
        return self.measurements.trips.get(self._ambulance_id)

    _ambulance_id: str = "ambulance_baseline"

    @property
    def valid_for_headline(self) -> tuple[bool, str]:
        """Whether this run may be used for a headline counterfactual number.

        Deliberately strict. Ambulance travel time is the headline metric, so a
        run in which the ambulance teleported or failed to arrive does not have
        one — and a permissive threshold here would let such a run into an
        average where nobody would find it again.
        """
        trip = self.ambulance
        if trip is None:
            return False, "the ambulance never entered the network"
        if trip.arrival_s is None:
            return False, "the ambulance did not complete its route"
        if trip.teleported:
            return False, (
                "the ambulance was teleported, so its travel time is not the time "
                "it spent driving its route"
            )
        if self.signal_conflicts:
            return False, (
                f"{len(self.signal_conflicts)} signal state(s) were observed that the "
                f"program does not define"
            )
        return True, "valid"

    def as_dict(self) -> dict[str, Any]:
        trip = self.ambulance
        valid, reason = self.valid_for_headline
        return {
            "policy": self.policy,
            "seed": self.seed,
            "valid_for_headline": valid,
            "validity_reason": reason,
            "output_dir": self.output_dir,
            "simulation": self.measurements.as_dict(),
            "ambulance": {
                "completed": trip is not None and trip.arrival_s is not None,
                "teleported": trip.teleported if trip else None,
                "travel_time_s": trip.travel_time_s if trip else None,
                "waiting_time_s": trip.waiting_time_s if trip else None,
                "time_loss_s": trip.time_loss_s if trip else None,
                "route_length_m": trip.route_length_m if trip else None,
                "stop_count": self.ambulance_stop_count,
                "route_edges": self.ambulance_route_edges,
                "per_edge_time_s": self.ambulance_edge_times,
                "signal_wait_events": self.ambulance_signal_waits,
            },
            "route_traffic_lights": self.route_tls,
            "policy_report": self.policy_report,
            "signal_conflicts": [c.as_dict() for c in self.signal_conflicts],
        }


def _import_traci(installation: SumoInstallation):
    tools = str(installation.tools_dir)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import traci  # noqa: PLC0415

    return traci


def run_policy(
    options: SumoRunOptions,
    policy: BasePolicy,
    ambulance_id: str,
    net_file: Path,
    ambulance_route: list[str],
    installation: SumoInstallation | None = None,
    sample_interval_s: float = 30.0,
) -> PolicyRunResult:
    """Run one scenario under one policy."""
    installation = installation or require_sumo()
    traci = _import_traci(installation)

    programs = load_programs(net_file)
    route_tls = traffic_lights_on_route(ambulance_route, programs)
    allowed_states = {tls_id: set(program.phase_states) for tls_id, program in programs.items()}
    watched = [entry.tls_id for entry in route_tls]

    port = free_port()
    command = build_sumo_command(options, traci_port=None, installation=installation)
    os.environ.update(sumo_environment(installation))

    measurements = RunMeasurements(sample_interval_s=sample_interval_s)
    conflicts: list[SignalConflict] = []
    edge_times: dict[str, float] = {}
    signal_waits: list[dict[str, Any]] = []
    stop_count = 0

    previous_edge: str | None = None
    edge_entered_at: float | None = None
    was_halted = False
    started = time.monotonic()

    try:
        traci.start(command, port=port)
        policy.on_simulation_start(route_tls, traci)

        route_positions = {edge: index for index, edge in enumerate(ambulance_route)}

        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            measurements.steps_executed += 1
            now = traci.simulation.getTime()
            measurements.sim_end_time_s = now
            if now >= options.end_s:
                break

            for vehicle_id in traci.simulation.getStartingTeleportIDList():
                edge_id = lane_id = None
                with contextlib.suppress(traci.TraCIException):
                    edge_id = traci.vehicle.getRoadID(vehicle_id)
                    lane_id = traci.vehicle.getLaneID(vehicle_id)
                measurements.teleports.append(
                    TeleportEvent(
                        vehicle_id=vehicle_id,
                        sim_time_s=now,
                        edge_id=edge_id,
                        lane_id=lane_id,
                        reason=(
                            "SUMO teleport: waited longer than --time-to-teleport "
                            f"({options.time_to_teleport_s:.0f}s), or was jammed."
                        ),
                    )
                )
                if vehicle_id in measurements.trips:
                    measurements.trips[vehicle_id].teleported = True

            measurements.collisions += traci.simulation.getCollidingVehiclesNumber()

            for vehicle_id in traci.simulation.getDepartedIDList():
                try:
                    measurements.trips[vehicle_id] = VehicleTrip(
                        vehicle_id=vehicle_id,
                        vehicle_type=traci.vehicle.getTypeID(vehicle_id),
                        depart_s=now,
                        route_edges=list(traci.vehicle.getRoute(vehicle_id)),
                    )
                except traci.TraCIException:
                    continue
                measurements.departed += 1

            for vehicle_id in traci.simulation.getArrivedIDList():
                measurements.arrived += 1
                trip = measurements.trips.get(vehicle_id)
                if trip is not None:
                    trip.arrival_s = now
                    if trip.depart_s is not None:
                        trip.travel_time_s = round(now - trip.depart_s, 3)
                if vehicle_id == ambulance_id and previous_edge is not None:
                    # Close the final edge here. Left to the end of the loop it
                    # would be measured to the simulation end rather than to
                    # arrival, putting a nonsense value into a reported metric.
                    if edge_entered_at is not None:
                        edge_times[previous_edge] = round(now - edge_entered_at, 2)
                    previous_edge = None
                    edge_entered_at = None

            # --- observe the ambulance (read only) -------------------------
            running = traci.vehicle.getIDList()
            observation = AmbulanceObservation(present=ambulance_id in running)
            if observation.present:
                try:
                    observation.edge_id = traci.vehicle.getRoadID(ambulance_id)
                    observation.lane_id = traci.vehicle.getLaneID(ambulance_id)
                    observation.lane_position_m = traci.vehicle.getLanePosition(ambulance_id)
                    observation.speed_ms = traci.vehicle.getSpeed(ambulance_id)
                    observation.route_index = route_positions.get(observation.edge_id)
                    for entry in route_tls:
                        distance = traci.vehicle.getDrivingDistance(
                            ambulance_id, entry.approach_edge, 0.0
                        )
                        observation.distance_to_tls_m[entry.tls_id] = distance
                except traci.TraCIException:
                    observation.present = False

                # per-edge traversal time, and stop / signal-wait events
                if observation.edge_id and not observation.edge_id.startswith(":"):
                    if observation.edge_id != previous_edge:
                        if previous_edge is not None and edge_entered_at is not None:
                            edge_times[previous_edge] = round(now - edge_entered_at, 2)
                        previous_edge = observation.edge_id
                        edge_entered_at = now
                    halted = (observation.speed_ms or 0.0) < 0.1
                    if halted and not was_halted:
                        stop_count += 1
                        near = [
                            entry.tls_id
                            for entry in route_tls
                            if 0 <= observation.distance_to_tls_m.get(entry.tls_id, 1e9) <= 60.0
                        ]
                        if near:
                            signal_waits.append(
                                {
                                    "sim_time_s": now,
                                    "edge_id": observation.edge_id,
                                    "tls_ids_within_60m": near,
                                    "lane_position_m": observation.lane_position_m,
                                }
                            )
                    was_halted = halted

            policy.on_step(now, observation, traci)

            # --- validate every watched signal against its program ---------
            for tls_id in watched:
                state = traci.trafficlight.getRedYellowGreenState(tls_id)
                if state not in allowed_states.get(tls_id, set()):
                    conflicts.append(
                        SignalConflict(
                            sim_time_s=now,
                            tls_id=tls_id,
                            observed_state=state,
                            reason=(
                                "observed signal state is not one of the program's "
                                "phases; a state written directly could combine "
                                "conflicting greens"
                            ),
                        )
                    )

            for vehicle_id in running:
                trip = measurements.trips.get(vehicle_id)
                if trip is None:
                    continue
                with contextlib.suppress(traci.TraCIException):
                    trip.waiting_time_s = traci.vehicle.getAccumulatedWaitingTime(vehicle_id)
                    trip.time_loss_s = traci.vehicle.getTimeLoss(vehicle_id)
                    trip.route_length_m = traci.vehicle.getDistance(vehicle_id)

        measurements.still_running_at_end = len(traci.vehicle.getIDList())
        measurements.insertion_backlog_at_end = len(traci.simulation.getPendingVehicles())
    finally:
        with contextlib.suppress(Exception):
            traci.close()
        measurements.wall_clock_s = time.monotonic() - started

    if previous_edge is not None and edge_entered_at is not None:
        # Only reached when the ambulance never arrived; the value is then the
        # time it had spent on that edge when the simulation ended, and the run
        # is already invalid for headline use.
        edge_times.setdefault(
            previous_edge, round(measurements.sim_end_time_s - edge_entered_at, 2)
        )

    result = PolicyRunResult(
        policy=policy.name,
        seed=options.seed,
        measurements=measurements,
        policy_report=policy.on_simulation_end(),
        route_tls=[entry.as_dict() for entry in route_tls],
        signal_conflicts=conflicts,
        ambulance_route_edges=list(ambulance_route),
        ambulance_edge_times=edge_times,
        ambulance_signal_waits=signal_waits,
        ambulance_stop_count=stop_count,
    )
    result._ambulance_id = ambulance_id
    return result


def route_tls_for(net_file: Path, route_edges: list[str]) -> list[RouteTls]:
    """Convenience: the traffic lights a route passes, with its links at each."""
    return traffic_lights_on_route(route_edges, load_programs(net_file))
