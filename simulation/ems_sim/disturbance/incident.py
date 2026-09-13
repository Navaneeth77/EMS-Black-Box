"""A lane blockage, applied to the network and never to a vehicle.

The disturbance this project needs is one that makes traffic worse in a way the
simulation itself produces. So the incident closes a lane — it does not place
queues, does not slow individual vehicles, and does not touch the ambulance.
Vehicles approaching the closed lane merge, the merge point saturates, and the
queue that forms is whatever SUMO's car-following and lane-change models
produce. Every vehicle in that queue got there by driving.

**What the incident does.** For its window, the affected lane is closed to all
vehicle classes and its speed limit is dropped. Closing alone is not enough:
`setDisallowed` stops vehicles *entering* the lane but says nothing about those
already on it, so without the speed drop the lane would drain at full speed and
the blockage would read as a gentle taper rather than an obstruction. Both are
restored exactly at the end of the window.

**What it deliberately does not do.** It does not insert a stopped vehicle. A
stopped vehicle would be a vehicle whose behaviour came from this module rather
than from the simulation, and it would appear in the trajectory recording as
traffic — which would make it indistinguishable, downstream, from traffic the
demand produced.

**Reproducibility.** The incident is a declared configuration with a hash. It is
applied at the same simulation times in every run, so a paired comparison across
policies differs in the policy and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

BLOCKED_CLASSES = (
    "passenger",
    "bus",
    "truck",
    "motorcycle",
    "delivery",
    "emergency",
    "taxi",
    "coach",
    "trailer",
    "moped",
    "bicycle",
    "pedestrian",
)
"""Classes barred from the lane while the incident is active.

`emergency` is included deliberately. The ambulance must meet the same road the
rest of the traffic meets: exempting it would hand it a private lane, and the
travel time that came back would be measuring the exemption rather than the
signal policy.
"""


@dataclass(frozen=True)
class IncidentConfig:
    """A declared, reproducible lane blockage.

    ``lane_index`` is an index into the edge's lanes. The edge keeps at least one
    open lane, so the blockage reduces capacity rather than severing the network:
    a severed route would make the ambulance's trip a different trip, and the
    comparison would no longer be about the policy.
    """

    incident_id: str
    incident_type: str
    start_time_s: float
    duration_s: float
    edge_id: str
    lane_index: int
    blockage: str
    speed_limit_ms: float
    description: str
    selection_basis: str
    data_class: str = "SIMULATED_SCENARIO"
    seed: int | None = None
    provenance: str = field(
        default=(
            "SIMULATED_SCENARIO. A hypothetical incident declared by this project "
            "to study traffic under disturbance. It is not a record of any real "
            "incident at Silk Board, and no incident report, traffic bulletin or "
            "observation was used. Its location and timing are configuration."
        )
    )

    @property
    def end_time_s(self) -> float:
        return self.start_time_s + self.duration_s

    @property
    def lane_id(self) -> str:
        return f"{self.edge_id}_{self.lane_index}"

    def active_at(self, sim_time_s: float) -> bool:
        return self.start_time_s <= sim_time_s < self.end_time_s

    def config_hash(self) -> str:
        """Identity of the disturbance, for the paired-run integrity check."""
        payload = {
            "incident_type": self.incident_type,
            "start_time_s": self.start_time_s,
            "duration_s": self.duration_s,
            "edge_id": self.edge_id,
            "lane_index": self.lane_index,
            "blockage": self.blockage,
            "speed_limit_ms": self.speed_limit_ms,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["lane_id"] = self.lane_id
        payload["end_time_s"] = self.end_time_s
        payload["config_hash"] = self.config_hash()
        return payload


# The committed default incident.
#
# **Lane choice, and the defect that forced it.** The first two configurations
# blocked `lane_index=0` — first on `92196679#0`, then on the signal approach
# `1393474724#1`. Both had *exactly zero* effect: not on the ambulance, and not
# on the network either. Vehicles-arrived was byte-identical with and without
# the incident across all 20 runs, which a lane closure in a 3,226-vehicle
# network cannot be.
#
# Measurement found the cause. On both edges **lane 0 carries no traffic at
# all**: `1393474724#1_0` sees 0 vehicles in the window while `_1` sees all 28;
# `92196679#0_0` sees 0 while `_1` sees all 26. Lane 0 on these approaches is
# not a lane traffic uses. Blocking it was blocking nothing.
#
# The rule is therefore **"the lane on the approach that actually carries the
# traffic"**, resolved by measurement rather than assumed to be lane 0.
#
# **And the blockage type changes with it.** Closing the only used lane could
# leave the edge's remaining lane without an onward connection and sever the
# route — and a severed route makes the ambulance's journey a different journey,
# not a delayed one. So this is a *partial obstruction*: the lane stays open and
# its speed drops to a crawl, which is what a stalled vehicle does. Traffic must
# edge past it, and whatever queue forms is SUMO's.
#
# None of this was chosen against the ambulance's travel time. The lane was
# chosen by vehicle count and the blockage type by network connectivity; the
# effect on the ambulance is whatever the following runs report.
#
# Timing: begins at 560 s, 40 s before the ambulance departs, so a queue has
# formed by the time it arrives rather than building around it. It runs 420 s,
# past the ambulance's undisturbed ~184 s trip, so the ambulance meets the
# disturbance for the whole of its journey.
DEFAULT_INCIDENT = IncidentConfig(
    incident_id="silk_board_lane_block_v1",
    incident_type="LANE_BLOCKAGE",
    start_time_s=560.0,
    duration_s=420.0,
    edge_id="1393474724#1",
    lane_index=1,
    blockage="partial_obstruction",
    speed_limit_ms=0.6,
    description=(
        "The trafficked lane of the approach to the GS_cluster traffic light is "
        "partially obstructed and drops to a crawl, at the stop line where the "
        "ambulance's delay is attributed. The lane stays open so the route is "
        "not severed."
    ),
    selection_basis=(
        "Edge: the approach of the actionable traffic light on the ambulance "
        "route. Lane: the one that carries the traffic - measured, because lane 0 "
        "on this approach carries no vehicles at all and blocking it did nothing. "
        "Blockage type: partial, because closing the only used lane risks severing "
        "the route. Every one of these was decided on flow and connectivity, not "
        "on any effect on ambulance travel time."
    ),
)


class IncidentController:
    """Applies and clears one incident during a TraCI run.

    Holds the lane's original allowances and speed limit so the restore is exact
    rather than a guess at what the defaults were.
    """

    def __init__(self, incident: IncidentConfig | None) -> None:
        self.incident = incident
        self._applied = False
        self._original_allowed: list[str] | None = None
        self._original_speed: float | None = None
        self.events: list[dict[str, Any]] = []

    @property
    def active(self) -> bool:
        return self._applied

    def on_step(self, sim_time_s: float, traci_module) -> None:
        if self.incident is None:
            return
        should_be_active = self.incident.active_at(sim_time_s)
        if should_be_active and not self._applied:
            self._apply(sim_time_s, traci_module)
        elif not should_be_active and self._applied:
            self._clear(sim_time_s, traci_module)

    def _apply(self, sim_time_s: float, traci_module) -> None:
        lane = self.incident.lane_id
        self._original_allowed = list(traci_module.lane.getAllowed(lane))
        self._original_speed = traci_module.lane.getMaxSpeed(lane)
        # A full closure bars the lane outright. A partial obstruction leaves it
        # open and drops its speed, which is what a stalled vehicle or debris
        # does: traffic must edge past rather than being turned away. The
        # partial form is used where the edge's other lane carries no traffic,
        # because closing the only used lane there could sever the route, and a
        # severed route makes the ambulance's trip a different trip.
        if self.incident.blockage == "full_lane_closure":
            traci_module.lane.setDisallowed(lane, list(BLOCKED_CLASSES))
        traci_module.lane.setMaxSpeed(lane, self.incident.speed_limit_ms)
        self._applied = True
        self.events.append(
            {
                "event": "incident_start",
                "sim_time_s": sim_time_s,
                "lane_id": lane,
                "original_speed_ms": self._original_speed,
                "applied_speed_ms": self.incident.speed_limit_ms,
            }
        )

    def _clear(self, sim_time_s: float, traci_module) -> None:
        lane = self.incident.lane_id
        if self._original_allowed is not None and self.incident.blockage == "full_lane_closure":
            traci_module.lane.setAllowed(lane, self._original_allowed)
        if self._original_speed is not None:
            traci_module.lane.setMaxSpeed(lane, self._original_speed)
        self._applied = False
        self.events.append({"event": "incident_end", "sim_time_s": sim_time_s, "lane_id": lane})

    def report(self) -> dict[str, Any]:
        if self.incident is None:
            return {"incident": None, "events": [], "applied": False}
        return {
            "incident": self.incident.as_dict(),
            "events": self.events,
            "applied": any(e["event"] == "incident_start" for e in self.events),
            "note": (
                "The incident closes a lane. No vehicle was moved, stopped, "
                "rerouted or re-timed by it; every queue is produced by SUMO's "
                "own car-following and lane-change models."
            ),
        }


class IncidentSet:
    """Several incidents driven together.

    Demo scenarios need congestion at more than one place, but a set is only
    meaningful if each member is a real disturbance in its own right: every one
    must sit on a lane that actually carries traffic. The set has its own hash,
    so a paired comparison can prove two runs met the *same* set rather than
    merely the same number of incidents.
    """

    def __init__(self, incidents: list[IncidentConfig] | None = None) -> None:
        self.incidents = incidents or []
        self.controllers = [IncidentController(i) for i in self.incidents]

    @property
    def edge_ids(self) -> list[str]:
        return [i.edge_id for i in self.incidents]

    def config_hash(self) -> str:
        joined = "|".join(sorted(i.config_hash() for i in self.incidents))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]

    def on_step(self, sim_time_s: float, traci_module) -> None:
        for controller in self.controllers:
            controller.on_step(sim_time_s, traci_module)

    @property
    def active(self) -> bool:
        return any(c.active for c in self.controllers)

    def report(self) -> dict[str, Any]:
        if not self.incidents:
            return {"incident": None, "incidents": [], "events": [], "applied": False}
        reports = [c.report() for c in self.controllers]
        events = [e for r in reports for e in r["events"]]
        return {
            # ``incident`` stays populated with the first entry so every existing
            # consumer of the single-incident shape keeps working unchanged.
            "incident": reports[0]["incident"],
            "incidents": [r["incident"] for r in reports],
            "set_config_hash": self.config_hash(),
            "events": sorted(events, key=lambda e: e["sim_time_s"]),
            "applied": all(r["applied"] for r in reports),
            "count": len(self.incidents),
            "note": (
                "Each incident closes or obstructs a lane that carries traffic. No "
                "vehicle was moved, stopped, rerouted or re-timed; every queue is "
                "produced by SUMO's own car-following and lane-change models."
            ),
        }
