"""Reproducible disturbances applied inside SUMO.

A disturbance is a change to the *road*, never to a vehicle. Nothing here moves,
stops, reroutes or re-times any individual vehicle; it closes or slows a lane and
lets the car-following and lane-changing models produce whatever queue follows.
That is the only way the resulting congestion can be evidence of anything.
"""

from ems_sim.disturbance.incident import (
    DEFAULT_INCIDENT,
    IncidentConfig,
    IncidentController,
)

__all__ = ["DEFAULT_INCIDENT", "IncidentConfig", "IncidentController"]
