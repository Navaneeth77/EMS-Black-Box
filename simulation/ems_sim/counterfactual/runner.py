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

from ems_sim.counterfactual.queues import queue_clearance_report
from ems_sim.disturbance.incident import IncidentConfig, IncidentController
from ems_sim.policies.base import AmbulanceObservation, BasePolicy
from ems_sim.policies.conflicts import ConflictWatcher, foe_matrix
from ems_sim.policies.tls_map import RouteTls, load_programs, traffic_lights_on_route
from ems_sim.runner.measurements import RunMeasurements, TeleportEvent, VehicleTrip
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo
from ems_sim.runner.sumo_process import (
    SumoRunOptions,
    build_sumo_command,
    free_port,
    sumo_environment,
)


def queue_summary(series: list[dict[str, Any]]) -> dict[str, Any]:
    """Max/mean queue over the run, and how long any queue persisted.

    ``recovery_time_s`` is the time from the incident clearing to the first
    sample whose halting count is back within the pre-incident band. It is only
    meaningful when there was an incident, and is ``None`` otherwise rather than
    a number that looks like one.
    """
    if not series:
        return {"samples": 0}
    halting = [s["halting"] for s in series]
    occupancy = [s.get("max_lane_occupancy", 0.0) for s in series]
    during = [s for s in series if s.get("incident_active")]
    before = [s["halting"] for s in series if not s.get("incident_active")]
    baseline = (sum(before) / len(before)) if before else 0.0

    recovery = None
    if during:
        end = max(s["sim_time_s"] for s in during)
        for sample in series:
            if sample["sim_time_s"] > end and sample["halting"] <= baseline * 1.2 + 1:
                recovery = round(sample["sim_time_s"] - end, 1)
                break
    return {
        "samples": len(series),
        "max_halting_vehicles": max(halting),
        "mean_halting_vehicles": round(sum(halting) / len(halting), 2),
        "max_lane_occupancy": round(max(occupancy), 4),
        "mean_lane_occupancy": round(sum(occupancy) / len(occupancy), 4),
        "baseline_halting_vehicles": round(baseline, 2),
        "peak_halting_during_incident": max((s["halting"] for s in during), default=None),
        "recovery_time_s": recovery,
        "source": "SUMO lane.getLastStepHaltingNumber / getLastStepOccupancy",
        "queue_length_note": (
            "Queue *length* in metres is taken from SUMO's own --queue-output, not "
            "from this series. getLastStepLength is mean vehicle length and must "
            "not be used for it."
        ),
    }


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
    incident_report: dict[str, Any] = field(default_factory=dict)
    queue_series: list[dict[str, Any]] = field(default_factory=list)
    queue_report: dict[str, Any] = field(default_factory=dict)
    ambulance_arrived_at_s: float | None = None
    signal_timeline: dict[str, list[list[Any]]] = field(default_factory=dict)
    conflict_report: dict[str, Any] = field(default_factory=dict)
    queue_by_tls: dict[str, list[list[float]]] = field(default_factory=dict)
    queue_clearance: dict[str, Any] = field(default_factory=dict)

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
        conflicts = self.conflict_report.get("conflict_count", 0)
        if conflicts:
            return False, (
                f"{conflicts} unsafe signal state(s) or change(s) were observed: "
                f"conflicting movements were given green together, or swapped without "
                f"clearance"
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
            "incident": self.incident_report,
            "ambulance_arrived_at_s": self.ambulance_arrived_at_s,
            "queues": {**queue_summary(self.queue_series), **self.queue_report},
            "queue_series": self.queue_series,
            "route_traffic_lights": self.route_tls,
            "policy_report": self.policy_report,
            "signal_conflicts": [c.as_dict() for c in self.signal_conflicts],
            "signal_conflict_check": self.conflict_report,
            "queue_clearance": self.queue_clearance,
        }


def _import_traci(installation: SumoInstallation):
    tools = str(installation.tools_dir)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import traci  # noqa: PLC0415

    return traci


def _queue_sample(traci_module, watched_lanes: list[str]) -> dict[str, float]:
    """Halting vehicles on the watched lanes, as SUMO counts them.

    ``getLastStepHaltingNumber`` is SUMO's own count of vehicles below its
    halting speed threshold. Nothing here infers a queue from positions.

    An earlier version of this also reported ``getLastStepLength`` as a queue
    length. That was wrong: ``getLastStepLength`` is the **mean length of the
    vehicles** on the lane, not the length of any queue, and it produced a
    "maximum queue" of 12 m — which was a bus. True queue length comes from
    SUMO's own ``--queue-output``, parsed after the run.
    """
    halting = 0
    occupancy = 0.0
    for lane in watched_lanes:
        with contextlib.suppress(Exception):
            halting += traci_module.lane.getLastStepHaltingNumber(lane)
            occupancy = max(occupancy, traci_module.lane.getLastStepOccupancy(lane))
    return {"halting": halting, "max_lane_occupancy": round(occupancy, 4)}


def _traffic_ahead(
    traci_module,
    ambulance_id: str,
    edge_id: str,
    position_m: float,
    downstream_edges: list[str],
) -> dict[str, Any]:
    """How much traffic stands between the ambulance and the signal ahead of it.

    Counts what SUMO already has on the road: vehicles further along the edge the
    ambulance is on, plus everything on the route edges between it and the
    approach it is heading for. Read-only, and only when a demo asks for it — no
    research run records it, and nothing here moves a vehicle.
    """
    ahead = 0
    halted = 0

    def tally(vehicle_ids, minimum_position: float | None) -> None:
        nonlocal ahead, halted
        for vehicle_id in vehicle_ids:
            if vehicle_id == ambulance_id:
                continue
            with contextlib.suppress(Exception):
                if (
                    minimum_position is not None
                    and traci_module.vehicle.getLanePosition(vehicle_id) <= minimum_position
                ):
                    continue
                ahead += 1
                if traci_module.vehicle.getSpeed(vehicle_id) < 0.1:
                    halted += 1

    with contextlib.suppress(Exception):
        tally(traci_module.edge.getLastStepVehicleIDs(edge_id), position_m)
    for downstream in downstream_edges:
        with contextlib.suppress(Exception):
            tally(traci_module.edge.getLastStepVehicleIDs(downstream), None)
    return {
        "edge_id": edge_id,
        "downstream_edges": list(downstream_edges),
        "count": ahead,
        "halted": halted,
    }


def run_policy(
    options: SumoRunOptions,
    policy: BasePolicy,
    ambulance_id: str,
    net_file: Path,
    ambulance_route: list[str],
    installation: SumoInstallation | None = None,
    sample_interval_s: float = 30.0,
    queue_sample_interval_s: float = 5.0,
    incident: IncidentConfig | None = None,
    stop_on_ambulance_arrival: bool = False,
    record_signal_timeline: bool = False,
    measure_distance_to_stop_line: bool = False,
    record_queue_ahead: bool = False,
) -> PolicyRunResult:
    """Run one scenario under one policy."""
    installation = installation or require_sumo()
    traci = _import_traci(installation)

    programs = load_programs(net_file)
    route_tls = traffic_lights_on_route(ambulance_route, programs)
    allowed_states = {tls_id: set(program.phase_states) for tls_id, program in programs.items()}
    watched = sorted({entry.tls_id for entry in route_tls})
    # Two independent checks on the signals, because they catch different faults.
    # `allowed_states` catches a state the program does not define at all — the
    # signature of something writing a state string directly. The watcher catches
    # a state that IS in the program but is unsafe against the junction's own foe
    # matrix, and unsafe changes between two states that are each legal.
    conflicts_watcher = ConflictWatcher(foe_matrix(net_file))

    port = free_port()
    command = build_sumo_command(options, traci_port=None, installation=installation)
    os.environ.update(sumo_environment(installation))

    measurements = RunMeasurements(sample_interval_s=sample_interval_s)
    conflicts: list[SignalConflict] = []
    queue_by_tls: dict[str, list[list[float]]] = {}
    edge_times: dict[str, float] = {}
    signal_waits: list[dict[str, Any]] = []
    stop_count = 0

    ambulance_arrived_at: float | None = None
    previous_edge: str | None = None
    edge_entered_at: float | None = None
    was_halted = False
    started = time.monotonic()

    # A single incident or a set; both expose on_step/report/edge_ids.
    incidents = (
        incident
        if incident is not None and hasattr(incident, "controllers")
        else IncidentController(incident)
    )
    # Queues are watched on the incident's own edge and on the ambulance's route
    # edges, because those are the two places the research makes claims about.
    queue_lanes: list[str] = []
    queue_series: list[dict[str, Any]] = []
    # Every traffic light's state as SUMO reports it, stored on change only.
    # The renderer needs the *applied* state: reconstructing it from the static
    # program is only faithful when no policy is acting, and an EMS run is
    # exactly the case where the policy holds and truncates phases.
    signal_timeline: dict[str, list[list[Any]]] = {}
    all_tls_ids: list[str] = []

    try:
        traci.start(command, port=port)
        policy.on_simulation_start(route_tls, traci)

        route_positions = {edge: index for index, edge in enumerate(ambulance_route)}
        if record_signal_timeline:
            all_tls_ids = list(traci.trafficlight.getIDList())

        watched_edges = list(ambulance_route)
        incident_edges = (
            list(incident.edge_ids)
            if incident is not None and hasattr(incident, "edge_ids")
            else ([incident.edge_id] if incident is not None else [])
        )
        for edge_id in incident_edges:
            if edge_id not in watched_edges:
                watched_edges.append(edge_id)
        for edge_id in watched_edges:
            with contextlib.suppress(Exception):
                count = traci.edge.getLaneNumber(edge_id)
                queue_lanes.extend(f"{edge_id}_{i}" for i in range(count))

        # Distance to each signal's STOP LINE, for the signal-wait detector only.
        #
        # ``getDrivingDistance(veh, edge, 0.0)`` measures to the *start* of the
        # approach edge. Once the ambulance is on that edge — which is the only
        # place it can queue at a red — the target is behind it and TraCI returns
        # INVALID_DOUBLE_VALUE (-2^30), so a ``0 <= d <= 60`` test can never fire.
        # The stop line is at the far end of the approach edge, so that is what a
        # "did a signal stop the ambulance" check has to measure to.
        #
        # Deliberately kept separate from ``observation.distance_to_tls_m``, which
        # the policies consume: changing what the policies see would change their
        # activation timing and make these runs incomparable with Phase 5's.
        stop_line_pos = {
            entry.tls_id: traci.lane.getLength(f"{entry.approach_edge}_0") for entry in route_tls
        }

        # Lanes to watch per signal, for the queue in front of it. The claim a
        # priority policy makes is that the queue discharges; measuring it means
        # counting the vehicles SUMO itself calls halted on the approach, not
        # inferring from the fact that the ambulance moved.
        approach_lanes: dict[str, list[str]] = {}
        for entry in route_tls:
            lanes: list[str] = []
            with contextlib.suppress(Exception):
                lanes = [
                    f"{entry.approach_edge}_{i}"
                    for i in range(traci.edge.getLaneNumber(entry.approach_edge))
                ]
            approach_lanes.setdefault(entry.tls_id, []).extend(lanes)
        queue_by_tls: dict[str, list[list[float]]] = {tls_id: [] for tls_id in approach_lanes}

        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            measurements.steps_executed += 1
            now = traci.simulation.getTime()
            measurements.sim_end_time_s = now
            if now >= options.end_s:
                break

            incidents.on_step(now, traci)
            if (
                queue_lanes
                and abs((now / sample_interval_s) - round(now / sample_interval_s)) < 1e-9
            ):
                sample = _queue_sample(traci, queue_lanes)
                sample["sim_time_s"] = now
                sample["incident_active"] = incidents.active
                queue_series.append(sample)

            # Demo mode ends the run at the first step after the ambulance
            # arrives. The research runs never do this: their traffic aggregate
            # is measured over the whole window, and truncating it would change
            # the denominator of every traffic metric.
            if stop_on_ambulance_arrival and ambulance_arrived_at is not None:
                measurements.sim_end_time_s = now
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
                if vehicle_id == ambulance_id:
                    # SUMO's own arrival event, not an inference from position.
                    ambulance_arrived_at = now
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
                        # Phase 5 measures to the *start* of the approach edge and its
                        # policies are calibrated on that, so it stays the default. A
                        # demo can ask for the stop line instead: that is where the
                        # queue ends, and where an approaching driver judges from.
                        target = (
                            stop_line_pos[entry.tls_id]
                            if measure_distance_to_stop_line
                            else 0.0
                        )
                        distance = traci.vehicle.getDrivingDistance(
                            ambulance_id, entry.approach_edge, target
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
                        near = {}
                        own_signal: dict[str, list[int]] = {}
                        for entry in route_tls:
                            to_stop_line = -1.0
                            with contextlib.suppress(traci.TraCIException):
                                to_stop_line = traci.vehicle.getDrivingDistance(
                                    ambulance_id,
                                    entry.approach_edge,
                                    stop_line_pos[entry.tls_id],
                                )
                            if 0 <= to_stop_line <= 60.0:
                                near[entry.tls_id] = round(to_stop_line, 1)
                                own_signal[entry.tls_id] = entry.ambulance_links
                        queue_ahead = None
                        if record_queue_ahead:
                            # Everything between the ambulance and the next signal on
                            # its route: the queue it is caught in usually reaches back
                            # over more than one edge.
                            index = route_positions.get(observation.edge_id)
                            target = next(
                                (
                                    entry
                                    for entry in route_tls
                                    if index is not None and entry.route_index >= index
                                ),
                                None,
                            )
                            downstream = (
                                ambulance_route[index + 1 : target.route_index + 1]
                                if target is not None and index is not None
                                else []
                            )
                            queue_ahead = _traffic_ahead(
                                traci,
                                ambulance_id,
                                observation.edge_id,
                                observation.lane_position_m or 0.0,
                                downstream,
                            )
                            if target is not None:
                                queue_ahead["towards_tls_id"] = target.tls_id
                        # Halting *near* a signal is not the same as being stopped
                        # *by* one. The ambulance can be held on a green approach by
                        # the queue in front of it, and counting that as signal delay
                        # would credit a priority policy with time it could never
                        # recover. Classify the halt by the state of the ambulance's
                        # own controlled links at that instant. When a demo asks for
                        # the queue ahead, halts *away* from any stop line are recorded
                        # too: being stuck at the back of a queue is the thing that
                        # demo is about, and it happens nowhere near the stop line.
                        if near or queue_ahead is not None:
                            states = {
                                tls_id: traci.trafficlight.getRedYellowGreenState(tls_id)
                                for tls_id in near
                            }
                            own_movement = {}
                            for tls_id, links in own_signal.items():
                                chars = [
                                    states[tls_id][i] for i in links if i < len(states[tls_id])
                                ]
                                own_movement[tls_id] = {
                                    "link_indices": links,
                                    "link_states": "".join(chars),
                                    "red": bool(chars) and all(c in "rR" for c in chars),
                                    "green": bool(chars) and any(c in "gG" for c in chars),
                                }
                            stopped_by_signal = any(m["red"] for m in own_movement.values())
                            if not near:
                                classification = "halted in traffic, away from any stop line"
                            elif stopped_by_signal:
                                classification = "held at red on the ambulance's own movement"
                            else:
                                classification = (
                                    "halted near a signal that was NOT red for the "
                                    "ambulance's movement — blocked by traffic ahead, not "
                                    "by the signal"
                                )
                            event = {
                                "sim_time_s": now,
                                "edge_id": observation.edge_id,
                                "tls_ids_within_60m": sorted(near),
                                "distance_to_stop_line_m": near,
                                "lane_position_m": observation.lane_position_m,
                                "tls_states": states,
                                "ambulance_own_movement": own_movement,
                                "stopped_by_signal": stopped_by_signal,
                                "classification": classification,
                            }
                            if queue_ahead is not None:
                                event["queue_ahead"] = queue_ahead
                                event["distance_to_tls_m"] = {
                                    tls_id: round(value, 1)
                                    for tls_id, value in observation.distance_to_tls_m.items()
                                    if value is not None and value >= 0
                                }
                            signal_waits.append(event)
                    was_halted = halted

            if approach_lanes and (
                abs((now / queue_sample_interval_s) - round(now / queue_sample_interval_s)) < 1e-9
            ):
                for tls_id, lanes in approach_lanes.items():
                    halting = 0
                    vehicles = 0
                    for lane in lanes:
                        with contextlib.suppress(Exception):
                            halting += traci.lane.getLastStepHaltingNumber(lane)
                            vehicles += traci.lane.getLastStepVehicleNumber(lane)
                    queue_by_tls[tls_id].append([now, halting, vehicles])

            # Record before the policy acts this step: the state read here is the
            # one that governed the movement SUMO just computed, so a vehicle and
            # the lamp beside it describe the same instant. Read-only; it cannot
            # change the simulation.
            for tls_id in all_tls_ids:
                current = traci.trafficlight.getRedYellowGreenState(tls_id)
                history = signal_timeline.setdefault(tls_id, [])
                if not history or history[-1][1] != current:
                    history.append([now, current])

            policy.on_step(now, observation, traci)

            # --- validate every watched signal against its program ---------
            for tls_id in watched:
                state = traci.trafficlight.getRedYellowGreenState(tls_id)
                conflicts_watcher.observe(tls_id, state, now)
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
    result.incident_report = incidents.report()
    result.conflict_report = conflicts_watcher.report()
    result.queue_by_tls = queue_by_tls
    result.queue_clearance = queue_clearance_report(result.policy_report, queue_by_tls)
    result.ambulance_arrived_at_s = ambulance_arrived_at
    result.signal_timeline = signal_timeline
    result.queue_series = queue_series
    result._ambulance_id = ambulance_id
    return result


def route_tls_for(net_file: Path, route_edges: list[str]) -> list[RouteTls]:
    """Convenience: the traffic lights a route passes, with its links at each."""
    return traffic_lights_on_route(route_edges, load_programs(net_file))
