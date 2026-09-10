"""Tests for vehicle type definitions.

These pin the honesty properties, not the numbers. The numbers are estimates and
are expected to change when someone sources better ones; what must not change is
that every one of them says it is an estimate and says why.
"""

from __future__ import annotations

import pytest
from ems_sim.demand.vehicle_types import (
    AMBULANCE,
    BACKGROUND_TYPES,
    VEHICLE_TYPES,
    get_vehicle_type,
    vehicle_types_provenance,
    vehicle_types_xml,
)
from ems_sim.provenance import DataClass


class TestCoverage:
    def test_every_required_class_is_defined(self) -> None:
        ids = {t.type_id for t in VEHICLE_TYPES}
        assert ids == {"car", "motorcycle", "auto", "bus", "truck", "van", "ambulance"}

    def test_vclasses_are_sumo_recognised(self) -> None:
        valid = {"passenger", "motorcycle", "bus", "truck", "delivery", "emergency"}
        for spec in VEHICLE_TYPES:
            assert spec.vclass in valid, spec.type_id

    def test_background_excludes_the_ambulance(self) -> None:
        assert "ambulance" not in {t.type_id for t in BACKGROUND_TYPES}
        assert len(BACKGROUND_TYPES) == len(VEHICLE_TYPES) - 1

    def test_lookup(self) -> None:
        assert get_vehicle_type("bus").vclass == "bus"
        with pytest.raises(KeyError, match="Unknown vehicle type"):
            get_vehicle_type("hovercraft")


class TestProvenance:
    """The property that matters: no parameter without a stated basis."""

    def test_every_parameter_has_a_basis(self) -> None:
        for spec in VEHICLE_TYPES:
            for name, parameter in spec.parameters.items():
                assert parameter.basis, f"{spec.type_id}.{name} has no basis"
                assert len(parameter.basis) > 20, f"{spec.type_id}.{name} basis is too thin"

    def test_every_parameter_is_labelled_estimated(self) -> None:
        """Nothing here was measured at Silk Board. If a value is ever sourced,
        its label changes and this test should be updated to reflect that."""
        for spec in VEHICLE_TYPES:
            for name, parameter in spec.parameters.items():
                assert parameter.data_class is DataClass.ESTIMATED, f"{spec.type_id}.{name}"

    def test_provenance_export_says_nothing_was_measured(self) -> None:
        payload = vehicle_types_provenance()
        assert payload["data_class"] == "ESTIMATED_DATA"
        assert "none was measured" in payload["summary"].lower()
        assert set(payload["types"]) == {t.type_id for t in VEHICLE_TYPES}

    def test_departures_from_sumo_defaults_say_so(self) -> None:
        """A value that differs from SUMO's default is a modelling choice, and
        the basis has to distinguish it from an inherited default."""
        motorcycle = get_vehicle_type("motorcycle")
        assert "departure" in motorcycle.parameters["lcAssertive"].basis.lower()


class TestPhysicalPlausibility:
    """Cheap guards against a typo producing a 45 m motorcycle."""

    def test_lengths_are_ordered_sensibly(self) -> None:
        length = {t.type_id: float(t.parameters["length"].value) for t in VEHICLE_TYPES}
        assert length["motorcycle"] < length["auto"] < length["car"]
        assert length["car"] < length["van"] < length["truck"] < length["bus"]

    def test_dimensions_are_positive_and_bounded(self) -> None:
        for spec in VEHICLE_TYPES:
            length = float(spec.parameters["length"].value)
            width = float(spec.parameters["width"].value)
            assert 1.0 < length < 20.0, spec.type_id
            assert 0.5 < width < 3.0, spec.type_id

    def test_two_wheelers_queue_closer_than_cars(self) -> None:
        """Drives saturation flow: a lane discharges more two-wheelers per green
        than lane count alone would predict."""
        assert float(get_vehicle_type("motorcycle").parameters["minGap"].value) < float(
            get_vehicle_type("car").parameters["minGap"].value
        )

    def test_deceleration_is_never_below_acceleration(self) -> None:
        for spec in VEHICLE_TYPES:
            assert float(spec.parameters["decel"].value) >= float(spec.parameters["accel"].value), (
                spec.type_id
            )


class TestAmbulanceHasNoPriorityYet:
    """Phase 3 is the baseline the Phase 5 counterfactual is subtracted from.

    A baseline ambulance that already had priority would make the recovered time
    reported later too small — the comparison would be against a head start.
    """

    def test_speed_factor_is_neutral(self) -> None:
        assert float(AMBULANCE.parameters["speedFactor"].value) == 1.0

    def test_no_priority_parameters_are_set(self) -> None:
        forbidden = {"jmIgnoreFoeProb", "jmIgnoreFoeSpeed", "jmIgnoreJunctionFoeProb"}
        assert not (forbidden & set(AMBULANCE.parameters))

    def test_description_states_it_obeys_normal_rules(self) -> None:
        assert "normal traffic rules" in AMBULANCE.description

    def test_uses_the_emergency_vclass(self) -> None:
        """vClass=emergency alone grants nothing in SUMO; rights come from
        devices or speed factors, neither of which is set."""
        assert AMBULANCE.vclass == "emergency"


class TestXmlOutput:
    def test_emits_one_vtype_per_definition(self) -> None:
        xml = vehicle_types_xml()
        assert xml.count("<vType ") == len(VEHICLE_TYPES)
        for spec in VEHICLE_TYPES:
            assert f'id="{spec.type_id}"' in xml

    def test_attributes_are_well_formed(self) -> None:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(f"<routes>{vehicle_types_xml()}</routes>")
        assert len(root.findall("vType")) == len(VEHICLE_TYPES)
