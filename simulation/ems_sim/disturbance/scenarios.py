"""Committed disturbance sets.

A set is only a disturbance if every member sits on a lane traffic actually
uses. That is not an assumption here — each lane below was chosen from measured
vehicle counts in the exported trajectory recording, after an earlier
configuration blocked a lane carrying **zero** vehicles and changed nothing at
all (see `COUNCIL_LOG.md` cycles 18-20).

`RESEARCH_INCIDENT` is the single obstruction behind the frozen 5-seed x 4-policy
experiment. **It must not change.** `DEMO_INCIDENT_SET` is a richer set used only
for presentation; it is a different scenario with its own hash and its own output
namespace, and it never touches the research outputs.
"""

from __future__ import annotations

from ems_sim.disturbance.incident import DEFAULT_INCIDENT, IncidentConfig, IncidentSet

RESEARCH_INCIDENT = DEFAULT_INCIDENT
"""The frozen research disturbance. Referenced, never modified."""


def _obstruction(
    incident_id: str,
    edge_id: str,
    lane_index: int,
    start_s: float,
    duration_s: float,
    observed_vehicles: int,
    where: str,
) -> IncidentConfig:
    return IncidentConfig(
        incident_id=incident_id,
        incident_type="LANE_BLOCKAGE",
        start_time_s=start_s,
        duration_s=duration_s,
        edge_id=edge_id,
        lane_index=lane_index,
        blockage="partial_obstruction",
        speed_limit_ms=0.6,
        description=f"Partial lane obstruction {where}.",
        selection_basis=(
            f"Lane {edge_id}_{lane_index} carries {observed_vehicles} vehicles in the "
            "exported window, measured from the trajectory recording. Lanes carrying "
            "no traffic are not used: obstructing one produces no queue and is not a "
            "disturbance. Partial rather than full closure so the lane stays "
            "connected and no route is severed."
        ),
    )


DEMO_INCIDENT_SET = IncidentSet(
    [
        # The signal approach the research attributes the ambulance's delay to.
        # Same lane as the research incident, so the demo's primary causal story
        # matches the experiment's.
        _obstruction(
            "demo_gs_approach",
            "1393474724#1",
            1,
            560.0,
            420.0,
            28,
            "on the GS_cluster signal approach carrying the ambulance",
        ),
        # A conflicting approach at the same signal. Queues here are what the
        # ambulance's green corridor has to hold back, so the cost of priority
        # becomes visible rather than implied.
        _obstruction(
            "demo_gs_conflicting",
            "464465165#3",
            1,
            520.0,
            500.0,
            18,
            "on a conflicting approach to the same signal",
        ),
        # Downstream of the signal: congestion the ambulance meets after it
        # clears the junction, so priority is visibly not a complete solution.
        _obstruction(
            "demo_downstream",
            "1148717038#0",
            1,
            540.0,
            460.0,
            39,
            "downstream of the GS_cluster junction on the ambulance route",
        ),
        # A later route edge, giving a second visible queue away from the
        # primary junction.
        _obstruction(
            "demo_route_tail",
            "1416769967",
            0,
            560.0,
            420.0,
            61,
            "on a later ambulance-route arterial",
        ),
    ]
)
"""Four simultaneous obstructions, all on measured-trafficked lanes.

Timings are staggered (520-560 s) so queues are already forming when the
ambulance departs at 600 s rather than building around it, and all run past its
arrival so it meets congestion for the whole trip.
"""
