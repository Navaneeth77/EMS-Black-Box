"""The TraCI control loop.

This is where "SUMO is the source of truth" is either enforced or quietly lost,
so the rules are worth stating before the code.

* **State is read from SUMO, never computed alongside it.** The moment this
  module maintains its own idea of where a vehicle is, there are two answers to
  every question and no way to tell which one a result came from.
* **The ambulance is an ordinary vehicle.** It is never moved with ``moveToXY``
  and never given priority in this phase. Its travel time is the headline number,
  and a number produced by nudging the vehicle is not a measurement.
* **Teleports are recorded, not suppressed.** SUMO teleports a vehicle that has
  been stuck too long. Turning teleporting off would not remove the gridlock, it
  would hide it — and a teleport that touches a measured trip invalidates that
  trip, so each one has to be attributable.

``traci`` and ``sumolib`` ship inside ``SUMO_HOME/tools`` rather than on PyPI and
must match the running SUMO: a pip-installed copy at a different version fails
with opaque protocol errors. They are imported at runtime from the resolved
installation.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ems_sim.runner.measurements import (
    RunMeasurements,
    StepSample,
    TeleportEvent,
    VehicleTrip,
)
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo
from ems_sim.runner.sumo_process import (
    SumoRunOptions,
    build_sumo_command,
    free_port,
    sumo_environment,
)


class SimulationError(RuntimeError):
    """Raised when a run cannot be completed or trusted."""


def _import_traci(installation: SumoInstallation):
    tools = str(installation.tools_dir)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import traci  # noqa: PLC0415

    return traci


def run_baseline(
    options: SumoRunOptions,
    ambulance_id: str,
    installation: SumoInstallation | None = None,
    sample_interval_s: float = 10.0,
    progress: Callable[[float, int], None] | None = None,
) -> RunMeasurements:
    """Run one baseline simulation under TraCI control.

    Advances SUMO step by step, reading state after each step. No policy acts on
    the simulation in this phase — there is nothing to actuate yet — but the loop
    is the same one Phase 5 will hang a signal policy off, so it is written to
    that shape now.
    """
    installation = installation or require_sumo()
    traci = _import_traci(installation)

    port = free_port()
    # traci.start launches SUMO and connects in one step. Starting the process
    # separately and then probing the port to see if it is ready does not work:
    # SUMO accepts one TraCI client, and the probe consumes it.
    command = build_sumo_command(options, traci_port=None, installation=installation)
    # traci.start inherits this process's environment, so SUMO_HOME and the PROJ
    # data directory have to be set here rather than passed to Popen.
    os.environ.update(sumo_environment(installation))
    measurements = RunMeasurements(sample_interval_s=sample_interval_s)

    started = time.monotonic()
    process = None
    try:
        traci.start(command, port=port)

        next_sample = options.begin_s
        seen_types: dict[str, str] = {}

        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            measurements.steps_executed += 1
            now = traci.simulation.getTime()
            measurements.sim_end_time_s = now

            if now >= options.end_s:
                break

            # --- teleports, read from SUMO's own event lists ------------------
            for vehicle_id in traci.simulation.getStartingTeleportIDList():
                edge_id = lane_id = None
                try:
                    edge_id = traci.vehicle.getRoadID(vehicle_id)
                    lane_id = traci.vehicle.getLaneID(vehicle_id)
                except traci.TraCIException:
                    # The vehicle is mid-teleport; position is not queryable.
                    pass
                measurements.teleports.append(
                    TeleportEvent(
                        vehicle_id=vehicle_id,
                        sim_time_s=now,
                        edge_id=edge_id,
                        lane_id=lane_id,
                        reason=(
                            "SUMO teleport: the vehicle waited longer than "
                            f"--time-to-teleport ({options.time_to_teleport_s:.0f}s), "
                            "or was jammed/collided. SUMO reports the trigger only "
                            "in its log, so the precise cause is recorded there."
                        ),
                        vehicle_type=seen_types.get(vehicle_id),
                    )
                )
                if vehicle_id in measurements.trips:
                    measurements.trips[vehicle_id].teleported = True

            measurements.collisions += traci.simulation.getCollidingVehiclesNumber()

            # --- departures: start a trip record ------------------------------
            for vehicle_id in traci.simulation.getDepartedIDList():
                try:
                    type_id = traci.vehicle.getTypeID(vehicle_id)
                    route = list(traci.vehicle.getRoute(vehicle_id))
                except traci.TraCIException:
                    continue
                seen_types[vehicle_id] = type_id
                measurements.trips[vehicle_id] = VehicleTrip(
                    vehicle_id=vehicle_id,
                    vehicle_type=type_id,
                    depart_s=now,
                    route_edges=route,
                )
                measurements.departed += 1

            # --- arrivals: close a trip record --------------------------------
            for vehicle_id in traci.simulation.getArrivedIDList():
                trip = measurements.trips.get(vehicle_id)
                measurements.arrived += 1
                if trip is not None:
                    trip.arrival_s = now
                    if trip.depart_s is not None:
                        trip.travel_time_s = round(now - trip.depart_s, 3)

            # --- running vehicles: accumulate what only TraCI can see ---------
            for vehicle_id in traci.vehicle.getIDList():
                trip = measurements.trips.get(vehicle_id)
                if trip is None:
                    continue
                try:
                    speed = traci.vehicle.getSpeed(vehicle_id)
                    trip.waiting_time_s = traci.vehicle.getAccumulatedWaitingTime(vehicle_id)
                    trip.time_loss_s = traci.vehicle.getTimeLoss(vehicle_id)
                    trip.route_length_m = traci.vehicle.getDistance(vehicle_id)
                except traci.TraCIException:
                    continue
                trip.max_speed_ms = (
                    speed if trip.max_speed_ms is None else max(trip.max_speed_ms, speed)
                )
                # Stop counts are not accumulated here. SUMO's tripinfo output
                # reports waitingCount per trip, computed by the simulator, and
                # counting halted steps in the loop would produce a different
                # number for the same events.

            # --- periodic sample ----------------------------------------------
            if now >= next_sample:
                next_sample += sample_interval_s
                running = traci.vehicle.getIDList()
                speeds = []
                halting = 0
                for vehicle_id in running:
                    try:
                        speed = traci.vehicle.getSpeed(vehicle_id)
                    except traci.TraCIException:
                        continue
                    speeds.append(speed)
                    if speed < 0.1:
                        halting += 1

                ambulance_speed = ambulance_edge = ambulance_waiting = None
                if ambulance_id in running:
                    try:
                        ambulance_speed = traci.vehicle.getSpeed(ambulance_id)
                        ambulance_edge = traci.vehicle.getRoadID(ambulance_id)
                        ambulance_waiting = traci.vehicle.getAccumulatedWaitingTime(ambulance_id)
                    except traci.TraCIException:
                        pass

                measurements.samples.append(
                    StepSample(
                        sim_time_s=now,
                        running_vehicles=len(running),
                        halting_vehicles=halting,
                        mean_speed_ms=(round(sum(speeds) / len(speeds), 4) if speeds else None),
                        teleports_cumulative=len(measurements.teleports),
                        ambulance_speed_ms=ambulance_speed,
                        ambulance_edge=ambulance_edge,
                        ambulance_waiting_s=ambulance_waiting,
                    )
                )
                if progress is not None:
                    progress(now, len(running))

        measurements.still_running_at_end = len(traci.vehicle.getIDList())
        measurements.insertion_backlog_at_end = len(traci.simulation.getPendingVehicles())

    finally:
        # Closing must never mask the real error from the loop above.
        with contextlib.suppress(Exception):
            traci.close()
        if process is not None:
            try:
                process.wait(timeout=30)
            except Exception:  # noqa: BLE001
                process.kill()
        measurements.wall_clock_s = time.monotonic() - started

    return measurements


def enrich_from_statistics(measurements: RunMeasurements, statistic_file: Path) -> RunMeasurements:
    """Take the vehicle totals from SUMO's own statistics output.

    Reconstructing them in the loop does not work: ``getLoadedNumber()`` is a
    per-step figure and misses vehicles loaded before the first step, which
    produced a loaded count *below* the departed count. SUMO writes the
    authoritative totals itself, so they are read rather than derived.
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    if not statistic_file.is_file():
        return measurements
    root = ET.parse(statistic_file).getroot()
    vehicles = root.find("vehicles")
    if vehicles is not None:
        measurements.loaded = int(vehicles.get("loaded", 0))
        measurements.departed = int(vehicles.get("inserted", measurements.departed))
        measurements.insertion_backlog_at_end = int(vehicles.get("waiting", 0))
    teleports = root.find("teleports")
    if teleports is not None:
        # SUMO's own teleport tally, cross-checked against the events the loop
        # observed. A mismatch means the loop missed some, which matters because
        # a teleport that touches a measured trip invalidates it.
        measurements.sumo_reported_teleports = int(teleports.get("total", 0))
    safety = root.find("safety")
    if safety is not None:
        measurements.collisions = int(safety.get("collisions", measurements.collisions))
    return measurements


def enrich_from_tripinfo(measurements: RunMeasurements, tripinfo_file: Path) -> RunMeasurements:
    """Fill in per-trip figures from SUMO's own tripinfo output.

    Preferred over the TraCI samples wherever both exist: tripinfo is SUMO's
    authoritative end-of-trip record, computed by the simulator rather than
    sampled by the loop. Vehicles absent from it did not complete, and are left
    with a null travel time rather than an inferred one.
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    if not tripinfo_file.is_file():
        return measurements

    root = ET.parse(tripinfo_file).getroot()
    for element in root.findall("tripinfo"):
        vehicle_id = element.get("id")
        trip = measurements.trips.get(vehicle_id)
        if trip is None:
            trip = VehicleTrip(vehicle_id=vehicle_id, vehicle_type=element.get("vType", "?"))
            measurements.trips[vehicle_id] = trip
        trip.depart_s = float(element.get("depart", "nan"))
        trip.arrival_s = float(element.get("arrival", "nan"))
        trip.travel_time_s = float(element.get("duration", "nan"))
        trip.waiting_time_s = float(element.get("waitingTime", "nan"))
        trip.time_loss_s = float(element.get("timeLoss", "nan"))
        trip.route_length_m = float(element.get("routeLength", "nan"))
        trip.stop_count = int(element.get("waitingCount", "0"))
    return measurements
