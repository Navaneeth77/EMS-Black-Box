"""The ambulance trip.

The ambulance is an ordinary SUMO vehicle with an ambulance vType. It is
inserted on a real edge, routed over the real network, and queues behind traffic
like anything else. It is never moved with ``moveToXY`` and never teleported.

**In Phase 3 it has no priority of any kind.** No siren, no signal preemption, no
speed bonus, no special right of way. That is the point: this run is the baseline
that Phase 5's counterfactual is subtracted from, and a baseline that already
carried some priority would understate what priority is worth — the headline
number would be quietly too small.

The origin and destination are a **configuration choice by this project**, not a
real EMS call. No ambulance dispatch record for Silk Board was used, because none
was available. The default is chosen so the trip crosses the junction under study
on the busiest corridor, which is what makes the counterfactual informative; it
is documented as such and is replaceable without touching code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ems_sim.provenance import DataClass


@dataclass(frozen=True)
class AmbulanceTripConfig:
    """A reproducible ambulance trip.

    ``vehicle_id`` is derived from the trip rather than random, so the same
    configuration always names the same vehicle and results can be joined across
    runs.
    """

    origin_edge: str
    destination_edge: str
    depart_time_s: float
    vehicle_type: str = "ambulance"
    trip_label: str = "baseline"
    basis: str = (
        "ESTIMATED_DATA. Origin, destination and departure time are configuration "
        "choices by this project, not a real EMS dispatch. No ambulance call "
        "record for Silk Board was available."
    )

    @property
    def vehicle_id(self) -> str:
        return f"ambulance_{self.trip_label}"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["vehicle_id"] = self.vehicle_id
        payload["data_class"] = str(DataClass.ESTIMATED)
        payload["priority_in_this_phase"] = (
            "none — obeys normal traffic rules; siren and signal preemption are Phase 5"
        )
        return payload


# The committed default trip.
#
# Hosur Road southern boundary to the Outer Ring Road western boundary: a
# south-to-west movement straight through the Silk Board interchange, on the two
# corridors that carry the most demand in the baseline. Chosen so the trip is
# exposed to the junction being studied — a route that avoided it would make the
# counterfactual measure nothing.
#
# Departure is 600 s: 300 s after the warm-up ends, so the ambulance enters a
# network that is already populated and queueing rather than filling up.
DEFAULT_AMBULANCE_TRIP = AmbulanceTripConfig(
    origin_edge="312063814#2",
    destination_edge="491889864#5",
    depart_time_s=600.0,
    trip_label="baseline",
)

# Phase 5 discovered that the trip above, despite being chosen to "cross the
# junction under study", produces a route through **zero** traffic lights: the
# router sends it down a residential rat-run south-east of Silk Board, which is
# also why its waiting time was 0.0 s in all five Phase 4 seeds.
#
# Signal priority cannot save time on a route with no signals, so a
# counterfactual run on it would show all four policies producing identical
# results — not because they are identical, but because none of them has
# anything to act on.
#
# This trip keeps the same Hosur Road origin and ends at the northern Sarjapura
# Road boundary instead. Its route passes three traffic lights, two of which have
# real red exposure for the ambulance's own movement. It is a configuration
# choice by this project, exactly as the Phase 3 trip was, and it is not a real
# EMS dispatch.
#
# The Phase 3 trip is left untouched: Phase 4's published numbers describe it,
# and changing it would invalidate them.
SIGNALISED_AMBULANCE_TRIP = AmbulanceTripConfig(
    origin_edge="312063814#2",
    destination_edge="1411121774#1",
    depart_time_s=600.0,
    trip_label="signalised",
    basis=(
        "ESTIMATED_DATA. Origin, destination and departure time are configuration "
        "choices by this project, not a real EMS dispatch. Selected in Phase 5 by "
        "searching boundary origin-destination pairs for a route that passes "
        "traffic lights with red exposure for the ambulance movement, because the "
        "Phase 3 trip's route passes none and no signal policy can act on it."
    ),
)
