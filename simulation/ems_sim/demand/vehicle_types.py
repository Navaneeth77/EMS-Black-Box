"""SUMO vehicle type definitions for Bengaluru mixed traffic.

**Every parameter here is ESTIMATED_DATA.** Nothing in this file was measured at
Silk Board or taken from a published Bengaluru study. They are engineering
placeholders chosen so the model exercises mixed traffic rather than a stream of
identical cars, and each one records the reasoning that produced it.

That framing matters more than the numbers. Vehicle dimensions and acceleration
set saturation flow and discharge rate at a junction, which set queue length,
which is what this project reports. A reader who believes these were surveyed
would read the results as far stronger than they are.

The distinction the labels draw:

* **Physical dimensions** (length, width) are typical values for the vehicle
  class — a Bajaj RE auto-rickshaw is about 2.6 m long — recorded as estimates
  because this project did not measure a fleet or cite a spec sheet.
* **Behavioural parameters** (accel, decel, sigma, tau, lane-change model) are
  SUMO defaults or deliberate departures from them. Departures are the
  interesting ones: ``lcAssertive`` and the sublane parameters on two-wheelers
  encode lane-filtering, which is the single largest way Indian junction
  behaviour differs from the European traffic SUMO's defaults were tuned on.

Replacing any of these with a sourced value is an improvement. Doing so changes
the label to PUBLICLY_SOURCED_DATA or VERIFIED_REAL_DATA and requires a rerun.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ems_sim.provenance import DataClass


@dataclass(frozen=True)
class Parameter:
    """One vehicle-type parameter, with where it came from."""

    value: float | str
    basis: str
    data_class: DataClass = DataClass.ESTIMATED

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "basis": self.basis, "data_class": str(self.data_class)}


@dataclass(frozen=True)
class VehicleTypeSpec:
    """A SUMO ``<vType>`` plus the provenance of every parameter."""

    type_id: str
    vclass: str
    description: str
    parameters: dict[str, Parameter]
    colour: str = "1,1,0"

    def xml_attributes(self) -> dict[str, str]:
        attributes = {"id": self.type_id, "vClass": self.vclass, "color": self.colour}
        attributes.update({k: str(p.value) for k, p in self.parameters.items()})
        return attributes

    def to_xml(self, indent: str = "        ") -> str:
        attributes = " ".join(f'{k}="{v}"' for k, v in self.xml_attributes().items())
        return f"{indent}<vType {attributes}/>"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.type_id,
            "vClass": self.vclass,
            "description": self.description,
            "parameters": {k: p.as_dict() for k, p in self.parameters.items()},
        }


# Shared bases, written once so the reasoning is stated rather than repeated.
_SUMO_DEFAULT = "SUMO's built-in default for this vehicle class; not changed by this project."
_DIMENSION = (
    "Typical dimension for the vehicle class. Not measured by this project and not "
    "taken from a manufacturer specification."
)
_MIXED_TRAFFIC = (
    "Departure from the SUMO default, chosen to represent Indian mixed-traffic "
    "behaviour (lane discipline weaker than the European traffic SUMO's defaults "
    "were calibrated on). Not calibrated against observation."
)
_SPEED_CAP = (
    "Upper bound on the vehicle's own capability, not a speed limit. The effective "
    "speed is the lower of this and the edge limit, so this only binds where the "
    "network permits more than the vehicle can do."
)


def _common(**overrides: Parameter) -> dict[str, Parameter]:
    """Car-following parameters shared by every type, before per-type overrides."""
    base = {
        "sigma": Parameter(
            0.5, "SUMO default driver imperfection. Retained: no basis for a different value."
        ),
        "tau": Parameter(1.0, "SUMO default desired headway in seconds. " + _SUMO_DEFAULT),
    }
    base.update(overrides)
    return base


PASSENGER_CAR = VehicleTypeSpec(
    type_id="car",
    vclass="passenger",
    description="Private car / taxi.",
    colour="0.8,0.8,0.9",
    parameters=_common(
        length=Parameter(4.5, _DIMENSION),
        width=Parameter(1.8, _DIMENSION),
        minGap=Parameter(2.0, "SUMO default standstill gap. " + _SUMO_DEFAULT),
        accel=Parameter(2.6, "SUMO default for passenger vehicles. " + _SUMO_DEFAULT),
        decel=Parameter(4.5, "SUMO default comfortable deceleration. " + _SUMO_DEFAULT),
        maxSpeed=Parameter(16.67, "60 km/h. " + _SPEED_CAP),
    ),
)

MOTORCYCLE = VehicleTypeSpec(
    type_id="motorcycle",
    vclass="motorcycle",
    description="Motorcycle or scooter. The dominant mode in Bengaluru traffic.",
    colour="0.9,0.5,0.1",
    parameters=_common(
        length=Parameter(2.2, _DIMENSION),
        width=Parameter(0.8, _DIMENSION),
        minGap=Parameter(
            0.8,
            "Smaller than the car default. Two-wheelers queue much closer together, "
            "which is why a junction discharges more of them per green than lane "
            "count alone would predict. " + _MIXED_TRAFFIC,
        ),
        accel=Parameter(3.0, "Higher than a car: lighter vehicle. " + _MIXED_TRAFFIC),
        decel=Parameter(6.0, "Higher than a car: lighter vehicle. " + _MIXED_TRAFFIC),
        maxSpeed=Parameter(16.67, "60 km/h. " + _SPEED_CAP),
        sigma=Parameter(0.6, "Slightly above the default. " + _MIXED_TRAFFIC),
        tau=Parameter(0.7, "Shorter headway than a car. " + _MIXED_TRAFFIC),
        lcAssertive=Parameter(
            2.0,
            "Well above the default of 1. Encodes lane-filtering: two-wheelers "
            "accept gaps a car would not. This is the largest single behavioural "
            "departure in this file and the one most in need of calibration. " + _MIXED_TRAFFIC,
        ),
        latAlignment=Parameter(
            "arbitrary",
            "Lateral position within the lane is unconstrained, so two-wheelers "
            "can share a lane laterally. Requires the sublane model to take "
            "effect; see BASELINE_SUMO_OPTIONS. " + _MIXED_TRAFFIC,
        ),
    ),
)

AUTO_RICKSHAW = VehicleTypeSpec(
    type_id="auto",
    vclass="passenger",
    description=(
        "Three-wheeled auto-rickshaw. Modelled as vClass=passenger because SUMO "
        "has no auto-rickshaw class and its road access rights match a car's."
    ),
    colour="0.95,0.85,0.1",
    parameters=_common(
        length=Parameter(2.6, _DIMENSION + " Based on the common Bajaj RE body size."),
        width=Parameter(1.3, _DIMENSION),
        minGap=Parameter(1.0, "Between a two-wheeler and a car. " + _MIXED_TRAFFIC),
        accel=Parameter(2.0, "Lower than a car: small engine. " + _MIXED_TRAFFIC),
        decel=Parameter(4.0, "Slightly below the car default. " + _MIXED_TRAFFIC),
        maxSpeed=Parameter(
            13.89,
            "50 km/h. Auto-rickshaws are slower than cars in practice. " + _SPEED_CAP,
        ),
        tau=Parameter(0.9, "Shorter headway than a car. " + _MIXED_TRAFFIC),
        lcAssertive=Parameter(1.5, "Between a car and a two-wheeler. " + _MIXED_TRAFFIC),
    ),
)

BUS = VehicleTypeSpec(
    type_id="bus",
    vclass="bus",
    description="Urban service bus (BMTC-size).",
    colour="0.2,0.5,0.9",
    parameters=_common(
        length=Parameter(12.0, _DIMENSION + " Standard two-axle urban bus."),
        width=Parameter(2.5, _DIMENSION),
        minGap=Parameter(2.5, "SUMO default for buses. " + _SUMO_DEFAULT),
        accel=Parameter(1.2, "SUMO default for buses. " + _SUMO_DEFAULT),
        decel=Parameter(4.0, "SUMO default for buses. " + _SUMO_DEFAULT),
        maxSpeed=Parameter(13.89, "50 km/h. " + _SPEED_CAP),
        tau=Parameter(1.4, "Longer headway than a car: heavier vehicle. " + _MIXED_TRAFFIC),
    ),
)

TRUCK = VehicleTypeSpec(
    type_id="truck",
    vclass="truck",
    description="Two-axle goods truck.",
    colour="0.4,0.3,0.2",
    parameters=_common(
        length=Parameter(7.5, _DIMENSION),
        width=Parameter(2.4, _DIMENSION),
        minGap=Parameter(2.5, "SUMO default for trucks. " + _SUMO_DEFAULT),
        accel=Parameter(1.3, "SUMO default for trucks. " + _SUMO_DEFAULT),
        decel=Parameter(4.0, "SUMO default for trucks. " + _SUMO_DEFAULT),
        maxSpeed=Parameter(13.89, "50 km/h. " + _SPEED_CAP),
        tau=Parameter(1.4, "Longer headway than a car. " + _MIXED_TRAFFIC),
    ),
)

VAN = VehicleTypeSpec(
    type_id="van",
    vclass="delivery",
    description="Light commercial van / delivery vehicle.",
    colour="0.6,0.6,0.6",
    parameters=_common(
        length=Parameter(5.5, _DIMENSION),
        width=Parameter(2.0, _DIMENSION),
        minGap=Parameter(2.0, "As for a car. " + _SUMO_DEFAULT),
        accel=Parameter(2.0, "Between a car and a truck. " + _MIXED_TRAFFIC),
        decel=Parameter(4.0, "Slightly below the car default. " + _MIXED_TRAFFIC),
        maxSpeed=Parameter(16.67, "60 km/h. " + _SPEED_CAP),
    ),
)

AMBULANCE = VehicleTypeSpec(
    type_id="ambulance",
    vclass="emergency",
    description=(
        "Emergency ambulance. In Phase 3 it obeys normal traffic rules: no siren "
        "behaviour, no signal preemption, no right of way beyond any other "
        "vehicle. That is deliberate — this run is the baseline the Phase 5 "
        "counterfactual is subtracted from, and a baseline that already carried "
        "some priority would understate what priority is worth."
    ),
    colour="1,0.1,0.1",
    parameters=_common(
        length=Parameter(6.0, _DIMENSION + " Van-based ambulance."),
        width=Parameter(2.2, _DIMENSION),
        minGap=Parameter(2.0, "As for a car; no special behaviour in the baseline."),
        accel=Parameter(2.4, "Slightly below a car: heavier vehicle. " + _MIXED_TRAFFIC),
        decel=Parameter(4.5, "As for a car. " + _SUMO_DEFAULT),
        maxSpeed=Parameter(19.44, "70 km/h. " + _SPEED_CAP),
        # SUMO grants vClass=emergency special rights only when a device or a
        # speed factor asks for them. Neither is set here, so the ambulance is an
        # ordinary vehicle that happens to be red.
        speedFactor=Parameter(
            1.0,
            "No speed bonus. Phase 3 is the baseline: the ambulance obeys the same "
            "rules as everything else, so that Phase 5's recovered time measures "
            "priority rather than a head start built into the baseline.",
        ),
    ),
)

VEHICLE_TYPES: tuple[VehicleTypeSpec, ...] = (
    PASSENGER_CAR,
    MOTORCYCLE,
    AUTO_RICKSHAW,
    BUS,
    TRUCK,
    VAN,
    AMBULANCE,
)

BACKGROUND_TYPES: tuple[VehicleTypeSpec, ...] = tuple(
    t for t in VEHICLE_TYPES if t.type_id != "ambulance"
)


def get_vehicle_type(type_id: str) -> VehicleTypeSpec:
    for spec in VEHICLE_TYPES:
        if spec.type_id == type_id:
            return spec
    known = ", ".join(t.type_id for t in VEHICLE_TYPES)
    raise KeyError(f"Unknown vehicle type {type_id!r}. Known: {known}")


def vehicle_types_xml(
    overrides: dict[str, dict[str, float | str]] | None = None,
) -> str:
    """The ``<vType>`` block, as written into the routes file.

    ``overrides`` maps a type id to parameter replacements, used by the Phase 4
    sensitivity experiments. An override is a deliberate departure from the
    committed baseline for one run, and it is recorded in that run's provenance —
    it never changes the committed definitions, so an experiment cannot silently
    become the baseline.
    """
    overrides = overrides or {}
    lines = []
    for spec in VEHICLE_TYPES:
        replacement = overrides.get(spec.type_id)
        if not replacement:
            lines.append(spec.to_xml())
            continue
        attributes = spec.xml_attributes()
        attributes.update({k: str(v) for k, v in replacement.items()})
        rendered = " ".join(f'{k}="{v}"' for k, v in attributes.items())
        lines.append(f"        <vType {rendered}/>")
    return "\n".join(lines)


def vehicle_types_provenance() -> dict[str, Any]:
    """Machine-readable provenance for every type and every parameter."""
    return {
        "data_class": str(DataClass.ESTIMATED),
        "summary": (
            "Every vehicle-type parameter is ESTIMATED_DATA. None was measured at "
            "Silk Board or taken from a published Bengaluru study. They are "
            "engineering placeholders chosen so the model exercises mixed traffic."
        ),
        "types": {spec.type_id: spec.as_dict() for spec in VEHICLE_TYPES},
    }
