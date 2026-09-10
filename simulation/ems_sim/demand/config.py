"""Baseline demand configuration.

Demand enters and leaves at the study-area boundary, because that is what the
network is: a 1.6 km box cut out of Bengaluru, whose edges continue beyond it.
Traffic is therefore expressed as flows between boundary **source** edges (no
incoming edge inside the box) and boundary **sink** edges (no outgoing edge).

**Every volume here is ESTIMATED_DATA.** No traffic count for Silk Board was
used, because none was available to this project. The numbers are chosen to
produce a congested-but-moving arterial network so the model exercises queueing
at the junction — they are not a claim about how many vehicles use Silk Board.

Two consequences follow, and both belong in any write-up:

* Absolute travel times from this demand describe *this modelled traffic*, not
  Bengaluru. They are a baseline to subtract a counterfactual from, not a
  measurement of congestion.
* Purely boundary-to-boundary flows omit local trips that begin or end inside
  the box. Real junction traffic includes both, so the mix here is skewed toward
  through-traffic.

Replacing the volumes with counted values is the single largest available
improvement to the realism of this model. Doing so changes their label and
requires a rerun.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from ems_sim.provenance import DataClass


class DemandPeriod(StrEnum):
    """Time-of-day periods the baseline supports."""

    MORNING_PEAK = "morning_peak"
    OFF_PEAK = "off_peak"
    EVENING_PEAK = "evening_peak"


# Scaling applied to the base flow rates per period.
#
# ESTIMATED_DATA. The ordering (peaks busier than off-peak, evening at least as
# busy as morning) reflects the ordinary shape of an urban weekday, but the
# magnitudes are assumptions. Directional asymmetry — heavier inbound in the
# morning, outbound in the evening — is deliberately NOT modelled, because doing
# so would require knowing which side of Silk Board the employment is on, and
# guessing that would be inventing a fact about the city.
PERIOD_SCALING: dict[DemandPeriod, float] = {
    DemandPeriod.MORNING_PEAK: 1.0,
    DemandPeriod.OFF_PEAK: 0.45,
    DemandPeriod.EVENING_PEAK: 1.0,
}

PERIOD_BASIS = (
    "ESTIMATED_DATA. Off-peak is set to 45% of peak as a plausible urban weekday "
    "shape; it is not derived from any count. The two peaks are equal because "
    "this project has no basis for making one heavier than the other, and "
    "inventing a directional asymmetry would be inventing a fact about where "
    "Bengaluru's employment sits relative to Silk Board."
)

# Share of background traffic by vehicle type.
#
# ESTIMATED_DATA, and the most conspicuous assumption in this file. Two-wheelers
# dominate because that is the widely observed character of Bengaluru traffic,
# but **this specific split is not taken from any survey or published modal
# share**. It exists so the model is mixed traffic rather than a stream of cars,
# and it must not be quoted as a modal split for Bengaluru.
#
# It matters because composition drives saturation flow: a lane discharges far
# more two-wheelers per green than cars, so the mix changes queue length and
# therefore every delay figure downstream.
VEHICLE_MIX: dict[str, float] = {
    "motorcycle": 0.50,
    "car": 0.26,
    "auto": 0.14,
    "bus": 0.03,
    "van": 0.04,
    "truck": 0.03,
}

VEHICLE_MIX_BASIS = (
    "ESTIMATED_DATA. Chosen so the simulation exercises mixed traffic, with "
    "two-wheelers dominant as is widely reported for Bengaluru. NOT a measured or "
    "published modal split, and must not be quoted as one. Composition drives "
    "saturation flow, so this assumption propagates into every queue length and "
    "delay figure the project reports."
)


@dataclass(frozen=True)
class BoundaryFlow:
    """One directed flow between a boundary source and a boundary sink."""

    from_edge: str
    to_edge: str
    vehicles_per_hour: float
    label: str
    basis: str = "ESTIMATED_DATA. See module docstring."

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DemandConfig:
    """Everything needed to regenerate a demand set byte-for-byte.

    Serialised alongside every result. The ``config_hash`` covers the demand
    inputs — flows, mix, seed, timing — so two runs can be shown to share a
    demand set, which is a precondition for comparing them.
    """

    demand_id: str
    period: DemandPeriod
    seed: int
    begin_s: float
    end_s: float
    flows: tuple[BoundaryFlow, ...]
    demand_scale: float = 1.0
    """Global multiplier on every flow rate.

    Calibrated, not chosen. The base rates were first set by judgement and the
    result gridlocked: at scale 1.0 the network produced 148 teleports and left
    955 vehicles unable to enter, which means nothing measured on it is a travel
    time for the journey it claims. The scale is the parameter that makes the
    demand meet its own stated basis — congested but moving — and the value is
    the largest that does, established by the sweep recorded in
    docs/TRAFFIC_DEMAND.md.
    """

    vehicle_mix: dict[str, float] = field(default_factory=lambda: dict(VEHICLE_MIX))
    step_length_s: float = 0.5
    warmup_s: float = 300.0

    def scaled_flows(self) -> tuple[BoundaryFlow, ...]:
        """Flows with the period scaling and the calibration scale applied."""
        scale = PERIOD_SCALING[self.period] * self.demand_scale
        return tuple(
            BoundaryFlow(
                from_edge=f.from_edge,
                to_edge=f.to_edge,
                vehicles_per_hour=round(f.vehicles_per_hour * scale, 3),
                label=f.label,
                basis=f.basis,
            )
            for f in self.flows
        )

    @property
    def total_vehicles_per_hour(self) -> float:
        return round(sum(f.vehicles_per_hour for f in self.scaled_flows()), 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "demand_id": self.demand_id,
            "period": str(self.period),
            "period_scaling": PERIOD_SCALING[self.period],
            "period_scaling_basis": PERIOD_BASIS,
            "demand_scale": self.demand_scale,
            "demand_scale_basis": DEMAND_SCALE_BASIS,
            "seed": self.seed,
            "begin_s": self.begin_s,
            "end_s": self.end_s,
            "warmup_s": self.warmup_s,
            "step_length_s": self.step_length_s,
            "vehicle_mix": self.vehicle_mix,
            "vehicle_mix_basis": VEHICLE_MIX_BASIS,
            "total_vehicles_per_hour": self.total_vehicles_per_hour,
            "flows": [f.as_dict() for f in self.scaled_flows()],
            "data_class": str(DataClass.ESTIMATED),
            "data_class_note": (
                "The demand configuration is ESTIMATED_DATA. Vehicles produced by "
                "running it are SIMULATED_DATA."
            ),
        }

    def config_hash(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Boundary terminals of the silk_board_v1 network.
#
# Verified against the built network rather than assumed: these are the edges
# with no incoming (sources) or no outgoing (sinks) edge inside the box, which is
# where a clipped network can accept and release traffic. Phase 2.5 identified
# four of them as flyover terminals; the rest are the surface corridors.
# ---------------------------------------------------------------------------

BOUNDARY_SOURCES: dict[str, str] = {
    "886153772": "Ragigudda-Silk Board Integrated Flyover, elevated, from the west",
    "631197944#1": "Sarjapura Road, from the north-east",
    "312063814#2": "Hosur Road, from the south",
    "380980308#0": "Outer Ring Road, from the east",
    "1109914039#1": "Outer Ring Road, from the west",
    "684917326#0": "Link near Madiwala, from the north-west",
    "148210030": "Madiwala Underpass, from the north-west",
    "1311812959#0": "Sarjapura Road, short northern stub",
}

BOUNDARY_SINKS: dict[str, str] = {
    "886153773": "Ragigudda flyover carriageway, to the west",
    "350096404#1": "Sarjapura Road, to the north-east",
    "1411121774#1": "Sarjapura Road, to the north",
    "1196514116#0": "Hosur Road, to the south",
    "1534247766#0": "Outer Ring Road, to the east",
    "491889864#5": "Outer Ring Road, to the west",
    "239438609": "Madiwala Underpass, to the north-west",
    "172853384#3": "Hosur Road link, to the north-west",
}


def _flow(from_edge: str, to_edge: str, vph: float, label: str) -> BoundaryFlow:
    return BoundaryFlow(from_edge=from_edge, to_edge=to_edge, vehicles_per_hour=vph, label=label)


# The committed OD structure.
#
# Each flow connects a boundary source to a boundary sink through the study area.
# The rates are ESTIMATED_DATA: they are set so the arterial corridors carry more
# than the minor ones and the network congests without gridlocking, which is the
# regime a junction study needs. They are not counts.
BASELINE_FLOWS: tuple[BoundaryFlow, ...] = (
    # --- Hosur Road (NH-44) corridor, the dominant north-south movement ------
    _flow("312063814#2", "491889864#5", 700, "Hosur Rd south -> ORR west"),
    _flow("312063814#2", "1534247766#0", 650, "Hosur Rd south -> ORR east"),
    _flow("312063814#2", "172853384#3", 500, "Hosur Rd south -> Hosur Rd north"),
    # --- Outer Ring Road corridor, the dominant east-west movement -----------
    _flow("380980308#0", "1196514116#0", 600, "ORR east -> Hosur Rd south"),
    _flow("380980308#0", "491889864#5", 550, "ORR east -> ORR west (through)"),
    _flow("1109914039#1", "1196514116#0", 550, "ORR west -> Hosur Rd south"),
    _flow("1109914039#1", "1534247766#0", 500, "ORR west -> ORR east (through)"),
    # --- Sarjapura Road, a secondary arterial --------------------------------
    _flow("631197944#1", "1196514116#0", 300, "Sarjapura Rd -> Hosur Rd south"),
    _flow("631197944#1", "491889864#5", 250, "Sarjapura Rd -> ORR west"),
    _flow("1311812959#0", "350096404#1", 150, "Sarjapura Rd local"),
    # --- Elevated corridor ----------------------------------------------------
    _flow("886153772", "1196514116#0", 400, "Ragigudda flyover -> Hosur Rd south"),
    _flow("886153772", "886153773", 350, "Ragigudda flyover through-movement"),
    # --- Madiwala side roads --------------------------------------------------
    _flow("148210030", "1534247766#0", 200, "Madiwala Underpass -> ORR east"),
    _flow("684917326#0", "1196514116#0", 180, "Madiwala link -> Hosur Rd south"),
)


DEMAND_SCALE_BASIS = (
    "ESTIMATED_DATA, calibrated against the model's own capacity rather than "
    "chosen. The base flow rates gridlocked this network at scale 1.0 — 148 "
    "teleports and 955 vehicles that never entered — and a gridlocked run reports "
    "travel times that are not the times to drive those journeys. The scale is the "
    "largest value at which the run stays within the teleport threshold and the "
    "insertion backlog stays small. It is a property of this model, not of "
    "Bengaluru: a network whose lane counts and signal timings were better sourced "
    "would carry a different volume."
)

DEFAULT_DEMAND_SCALE = 0.5
"""Calibrated, not chosen. See docs/TRAFFIC_DEMAND.md for the sweep.

At 1800 s of evening peak, scale 0.35 gave 0 teleports but a nearly free-flowing
network (24.8 s mean time loss), 0.5 gave 0 teleports with the network congested
and moving (60.2 s), and 0.65 gave 13 — above the threshold. The base rates at
scale 1.0 gridlocked it outright: 148 teleports and 955 vehicles that never
entered.

0.5 is the largest scale that keeps every recorded travel time a time somebody
actually drove.
"""

DEFAULT_SEED = 20260910
"""Documented seed for the baseline. Any value would do; what matters is that it
is recorded, so a run can be reproduced rather than approximated."""


def make_config(
    period: DemandPeriod = DemandPeriod.EVENING_PEAK,
    seed: int = DEFAULT_SEED,
    duration_s: float = 3600.0,
    warmup_s: float = 300.0,
    step_length_s: float = 0.5,
    demand_scale: float = DEFAULT_DEMAND_SCALE,
) -> DemandConfig:
    """Build a demand configuration.

    Defaults to the weekday evening peak, as specified for the baseline.
    ``begin_s`` is 0 and demand runs for ``warmup_s + duration_s``: the warm-up
    populates the network so the measured hour starts with traffic already on it
    rather than on an empty map.
    """
    scale_tag = f"_s{demand_scale:g}".replace(".", "p") if demand_scale != 1.0 else ""
    return DemandConfig(
        demand_id=f"silk_board_v1_{period}_seed{seed}{scale_tag}",
        period=period,
        seed=seed,
        begin_s=0.0,
        end_s=warmup_s + duration_s,
        warmup_s=warmup_s,
        step_length_s=step_length_s,
        flows=BASELINE_FLOWS,
        demand_scale=demand_scale,
    )
