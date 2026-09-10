"""What the simulation records, and what it refuses to record.

Every field here is read from SUMO. Nothing is derived from an assumption, and
nothing is filled in when SUMO does not supply it — a vehicle that never arrived
has no travel time, and this module leaves that as ``None`` rather than
substituting the simulation end time. A missing measurement is a fact about the
run; a substituted one is a fabrication that looks like data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TeleportEvent:
    """One SUMO teleport, recorded in full.

    A teleport means a vehicle was stuck long enough that SUMO moved it rather
    than let it block the network forever. It is a symptom of gridlock, and a
    teleport that touches a measured trip invalidates that trip's travel time —
    the vehicle did not drive the distance it was credited with.
    """

    vehicle_id: str
    sim_time_s: float
    edge_id: str | None
    lane_id: str | None
    reason: str
    vehicle_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VehicleTrip:
    """Per-vehicle measurements, as SUMO reported them."""

    vehicle_id: str
    vehicle_type: str
    depart_s: float | None = None
    arrival_s: float | None = None
    route_edges: list[str] = field(default_factory=list)

    travel_time_s: float | None = None
    waiting_time_s: float | None = None
    time_loss_s: float | None = None
    route_length_m: float | None = None
    stop_count: int = 0
    max_speed_ms: float | None = None
    mean_speed_ms: float | None = None
    teleported: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["completed"] = self.arrival_s is not None
        return payload


@dataclass
class StepSample:
    """One sampled simulation step.

    Sampled rather than recorded every step: at 0.5 s over 3900 s a full record
    is 7800 frames, most of which say the same thing. The sampling interval is
    recorded so the series can be interpreted.
    """

    sim_time_s: float
    running_vehicles: int
    halting_vehicles: int
    mean_speed_ms: float | None
    teleports_cumulative: int
    ambulance_speed_ms: float | None = None
    ambulance_edge: str | None = None
    ambulance_waiting_s: float | None = None


@dataclass
class RunMeasurements:
    """Everything one baseline run produced."""

    teleports: list[TeleportEvent] = field(default_factory=list)
    trips: dict[str, VehicleTrip] = field(default_factory=dict)
    samples: list[StepSample] = field(default_factory=list)
    sample_interval_s: float = 10.0

    steps_executed: int = 0
    sim_end_time_s: float = 0.0
    wall_clock_s: float = 0.0

    departed: int = 0
    arrived: int = 0
    loaded: int = 0
    still_running_at_end: int = 0
    insertion_backlog_at_end: int = 0
    collisions: int = 0
    sumo_reported_teleports: int | None = None
    """SUMO's own teleport tally, for cross-checking the loop's observations."""

    def as_dict(self) -> dict[str, Any]:
        completed = [t for t in self.trips.values() if t.arrival_s is not None]
        return {
            "steps_executed": self.steps_executed,
            "sim_end_time_s": self.sim_end_time_s,
            "wall_clock_s": round(self.wall_clock_s, 2),
            "sample_interval_s": self.sample_interval_s,
            "vehicles": {
                "loaded": self.loaded,
                "departed": self.departed,
                "arrived": self.arrived,
                "still_running_at_end": self.still_running_at_end,
                "insertion_backlog_at_end": self.insertion_backlog_at_end,
                "trips_recorded": len(self.trips),
                "trips_completed": len(completed),
            },
            "teleports": {
                "count": len(self.teleports),
                "sumo_reported_total": self.sumo_reported_teleports,
                "observations_match_sumo": (
                    self.sumo_reported_teleports is None
                    or self.sumo_reported_teleports == len(self.teleports)
                ),
                "events": [t.as_dict() for t in self.teleports[:200]],
            },
            "collisions": self.collisions,
        }
