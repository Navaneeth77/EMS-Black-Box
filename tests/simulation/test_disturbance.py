"""Tests for the lane-blockage disturbance.

The disturbance exists to make traffic worse in a way the simulation produces.
Two properties make that claim defensible, and both are defended here:

* **It touches the road, never a vehicle.** The controller's only effects are on
  a lane's allowances and speed limit. If it ever moved, stopped or rerouted a
  vehicle, the resulting queue would be partly this module's output rather than
  SUMO's.
* **It is identical across the policies of a seed.** A paired counterfactual is
  only valid if the disturbance is part of the shared scenario, so its config
  hash has to be stable and sensitive to every field that changes its effect.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from ems_sim.disturbance.incident import (
    BLOCKED_CLASSES,
    DEFAULT_INCIDENT,
    SEVERITY_SPEED_LIMIT_MS,
    IncidentConfig,
    IncidentController,
    incident_at_severity,
)


class FakeLane:
    """Records what was done to it. Nothing else in SUMO is reachable from here."""

    def __init__(self) -> None:
        self.allowed = ["passenger", "bus", "emergency"]
        self.max_speed = 27.78
        self.calls: list[tuple[str, object]] = []

    def getAllowed(self, _lane):  # noqa: N802 - mirrors the TraCI API
        return list(self.allowed)

    def getMaxSpeed(self, _lane):  # noqa: N802
        return self.max_speed

    def setDisallowed(self, _lane, classes):  # noqa: N802
        self.calls.append(("setDisallowed", tuple(classes)))
        self.allowed = []

    def setAllowed(self, _lane, classes):  # noqa: N802
        self.calls.append(("setAllowed", tuple(classes)))
        self.allowed = list(classes)

    def setMaxSpeed(self, _lane, speed):  # noqa: N802
        self.calls.append(("setMaxSpeed", speed))
        self.max_speed = speed


class FakeTraci:
    def __init__(self) -> None:
        self.lane = FakeLane()


@pytest.fixture
def incident() -> IncidentConfig:
    return IncidentConfig(
        incident_id="t",
        incident_type="LANE_BLOCKAGE",
        start_time_s=100.0,
        duration_s=50.0,
        edge_id="e1",
        lane_index=0,
        blockage="full_lane_closure",
        speed_limit_ms=0.6,
        description="d",
        selection_basis="b",
    )


class TestWindow:
    def test_active_only_inside_the_window(self, incident: IncidentConfig) -> None:
        assert not incident.active_at(99.9)
        assert incident.active_at(100.0)
        assert incident.active_at(149.9)
        assert not incident.active_at(150.0)  # end is exclusive

    def test_lane_id_is_edge_plus_index(self, incident: IncidentConfig) -> None:
        assert incident.lane_id == "e1_0"


class TestController:
    def test_applies_once_and_clears_once(self, incident: IncidentConfig) -> None:
        traci = FakeTraci()
        controller = IncidentController(incident)
        for t in (50.0, 99.5, 100.0, 120.0, 149.5, 150.0, 200.0):
            controller.on_step(t, traci)
        kinds = [e["event"] for e in controller.events]
        assert kinds == ["incident_start", "incident_end"]

    def test_restores_the_exact_original_state(self, incident: IncidentConfig) -> None:
        """Restoring to a guessed default would leave the network subtly changed."""
        traci = FakeTraci()
        original_allowed = traci.lane.getAllowed("e1_0")
        original_speed = traci.lane.getMaxSpeed("e1_0")
        controller = IncidentController(incident)
        controller.on_step(100.0, traci)
        assert traci.lane.max_speed == pytest.approx(0.6)
        controller.on_step(150.0, traci)
        assert traci.lane.getAllowed("e1_0") == original_allowed
        assert traci.lane.max_speed == pytest.approx(original_speed)

    def test_never_touches_a_vehicle(self, incident: IncidentConfig) -> None:
        """The controller has no vehicle API available to it at all.

        If a future change reached for one, this fails rather than quietly
        producing queues that are partly this module's doing.
        """
        traci = FakeTraci()
        controller = IncidentController(incident)
        controller.on_step(100.0, traci)
        controller.on_step(150.0, traci)
        assert not hasattr(traci, "vehicle")
        assert all(call[0].startswith("set") for call in traci.lane.calls)

    def test_blocks_emergency_too(self) -> None:
        """The ambulance must meet the same road as everyone else.

        Exempting it would hand it a private lane, and the travel time would be
        measuring the exemption rather than the signal policy.
        """
        assert "emergency" in BLOCKED_CLASSES

    def test_no_incident_is_a_no_op(self) -> None:
        traci = FakeTraci()
        controller = IncidentController(None)
        controller.on_step(100.0, traci)
        assert controller.events == []
        assert traci.lane.calls == []
        assert controller.report()["incident"] is None


class TestIdentity:
    def test_hash_is_stable(self, incident: IncidentConfig) -> None:
        assert incident.config_hash() == replace(incident, description="other").config_hash()

    @pytest.mark.parametrize(
        "field",
        ["start_time_s", "duration_s", "edge_id", "lane_index", "speed_limit_ms"],
    )
    def test_hash_changes_when_the_effect_changes(
        self, incident: IncidentConfig, field: str
    ) -> None:
        """Anything that alters what the incident does must alter its identity.

        Two policy runs are only a pair if their disturbance is the same one, and
        the integrity check compares these hashes.
        """
        altered = {
            "start_time_s": 111.0,
            "duration_s": 7.0,
            "edge_id": "other",
            "lane_index": 1,
            "speed_limit_ms": 5.0,
        }[field]
        assert replace(incident, **{field: altered}).config_hash() != incident.config_hash()


class TestCommittedDefault:
    def test_is_labelled_a_simulated_scenario(self) -> None:
        assert DEFAULT_INCIDENT.data_class == "SIMULATED_SCENARIO"
        assert "not a record of any real incident" in DEFAULT_INCIDENT.provenance.lower()

    def test_states_why_that_location_was_chosen(self) -> None:
        assert DEFAULT_INCIDENT.selection_basis.strip()

    def test_begins_before_the_ambulance_departs(self) -> None:
        """A queue has to exist before the ambulance arrives, or it builds around it."""
        assert DEFAULT_INCIDENT.start_time_s < 600.0
        assert DEFAULT_INCIDENT.end_time_s > 600.0 + 184.5


class TestSeverityLadder:
    """The CONGESTION_SEVERITY sweep must move one number and nothing else.

    A partial obstruction's only effect is the lane's speed limit, so that number
    is the obstruction's severity. If the sweep changed anything else — the edge,
    the lane, the window, the blockage type — the experiment would no longer be
    about queue severity.
    """

    def test_medium_is_the_committed_disturbance_itself(self) -> None:
        """Not a re-creation of it: the same object, so the same scenario hash."""
        assert incident_at_severity("medium") is DEFAULT_INCIDENT

    @pytest.mark.parametrize("severity", sorted(SEVERITY_SPEED_LIMIT_MS))
    def test_only_the_discharge_rate_differs(self, severity: str) -> None:
        incident = incident_at_severity(severity)
        for field in ("start_time_s", "duration_s", "edge_id", "lane_index", "blockage"):
            assert getattr(incident, field) == getattr(DEFAULT_INCIDENT, field)
        assert incident.speed_limit_ms == SEVERITY_SPEED_LIMIT_MS[severity]

    def test_the_ladder_is_monotone_in_severity(self) -> None:
        """Lower speed is a tighter obstruction, so the order must not be scrambled."""
        low, medium, high = (
            incident_at_severity(s).speed_limit_ms for s in ("low", "medium", "high")
        )
        assert low > medium > high

    def test_each_severity_is_a_distinct_scenario(self) -> None:
        """Different disturbances must not pair with each other."""
        hashes = {s: incident_at_severity(s).config_hash() for s in SEVERITY_SPEED_LIMIT_MS}
        assert len(set(hashes.values())) == len(hashes)

    def test_severities_are_labelled_assumed_not_observed(self) -> None:
        for severity in SEVERITY_SPEED_LIMIT_MS:
            incident = incident_at_severity(severity)
            assert incident.data_class == "SIMULATED_SCENARIO"
            if severity != "medium":
                assert "ASSUMED_SCENARIO_PARAMETER" in incident.selection_basis
                assert "not observed" in incident.selection_basis.lower()

    def test_an_unknown_severity_is_refused(self) -> None:
        with pytest.raises(KeyError, match="Unknown severity"):
            incident_at_severity("catastrophic")
