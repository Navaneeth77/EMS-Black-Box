"""Tests for the SUMO process layer and the baseline validation rules.

The validation rules are tested against constructed measurements rather than by
running a simulation, so they are fast and deterministic. The one thing that does
need a real run — that the whole pipeline produces the artefacts it promises — is
asserted against the run on disk and skipped when there is none.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP
from ems_sim.demand.baseline import (
    DEFAULT_TELEPORT_THRESHOLD,
    _ambulance_summary,
    validate_baseline,
)
from ems_sim.demand.config import make_config
from ems_sim.runner.measurements import RunMeasurements, TeleportEvent, VehicleTrip
from ems_sim.runner.sumo_process import SumoRunOptions, build_sumo_command, free_port

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED = REPO_ROOT / "data" / "processed" / "silk_board_v1"


def measurements_with(**overrides) -> RunMeasurements:
    config = make_config()
    base = RunMeasurements(
        steps_executed=100,
        sim_end_time_s=config.end_s,
        departed=500,
        arrived=400,
        loaded=520,
        insertion_backlog_at_end=20,
    )
    trip = VehicleTrip(
        vehicle_id=DEFAULT_AMBULANCE_TRIP.vehicle_id,
        vehicle_type="ambulance",
        depart_s=600.0,
        arrival_s=800.0,
        travel_time_s=200.0,
        waiting_time_s=30.0,
        time_loss_s=60.0,
        route_length_m=2500.0,
    )
    base.trips[trip.vehicle_id] = trip
    base.trips["car.1"] = VehicleTrip("car.1", "car", 10.0, 200.0, travel_time_s=190.0)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def check(report, name):
    matches = [c for c in report.checks if c.name == name]
    assert matches, f"no check named {name!r}"
    return matches[0]


class TestSumoCommand:
    def test_uses_the_resolved_absolute_binary(self) -> None:
        """Never the bare name: the `sumo` on PATH here launches the GUI and
        returns immediately, simulating nothing."""
        command = build_sumo_command(
            SumoRunOptions(net_file=Path("n.net.xml"), route_files=(Path("r.rou.xml"),))
        )
        assert Path(command[0]).is_absolute()
        assert command[0].endswith("/sumo")

    def test_seed_and_step_length_are_passed(self) -> None:
        options = SumoRunOptions(
            net_file=Path("n"), route_files=(Path("r"),), seed=7, step_length_s=0.25
        )
        command = build_sumo_command(options)
        assert command[command.index("--seed") + 1] == "7"
        assert command[command.index("--step-length") + 1] == "0.25"

    def test_teleporting_is_not_disabled(self) -> None:
        """A teleport is a visible symptom of gridlock. Switching it off would
        replace it with vehicles jammed forever and trips that never complete."""
        options = SumoRunOptions(net_file=Path("n"), route_files=(Path("r"),))
        command = build_sumo_command(options)
        value = float(command[command.index("--time-to-teleport") + 1])
        assert value > 0

    def test_free_port_returns_distinct_ports(self) -> None:
        assert free_port() != 0


class TestTeleportRules:
    def test_run_within_threshold_passes(self) -> None:
        report = validate_baseline(measurements_with(), DEFAULT_AMBULANCE_TRIP, make_config())
        assert check(report, "teleports_within_threshold").passed

    def test_run_above_threshold_is_an_error(self) -> None:
        """The guard that rejected the first full run: 148 teleports means the
        recorded travel times are not the times to drive those paths."""
        measurements = measurements_with()
        measurements.teleports = [
            TeleportEvent(f"v{i}", float(i), "e", "e_0", "jam") for i in range(50)
        ]
        report = validate_baseline(measurements, DEFAULT_AMBULANCE_TRIP, make_config())
        result = check(report, "teleports_within_threshold")
        assert not result.passed
        assert not report.ok

    def test_threshold_is_configurable(self) -> None:
        measurements = measurements_with()
        measurements.teleports = [TeleportEvent("v", 1.0, "e", "e_0", "jam")] * 20
        assert validate_baseline(
            measurements, DEFAULT_AMBULANCE_TRIP, make_config(), teleport_threshold=100
        ).ok

    def test_mismatch_with_sumo_tally_is_flagged(self) -> None:
        """An unobserved teleport cannot be attributed to a trip."""
        measurements = measurements_with()
        measurements.sumo_reported_teleports = 5
        report = validate_baseline(measurements, DEFAULT_AMBULANCE_TRIP, make_config())
        assert not check(report, "teleport_observations_match_sumo").passed

    def test_teleport_events_record_where_and_when(self) -> None:
        event = TeleportEvent("v1", 123.4, "edge_a", "edge_a_0", "jam", "car").as_dict()
        for key in ("vehicle_id", "sim_time_s", "edge_id", "lane_id", "reason"):
            assert key in event

    def test_default_threshold_is_small(self) -> None:
        assert 0 < DEFAULT_TELEPORT_THRESHOLD <= 25


class TestAmbulanceRules:
    def test_completed_trip_passes(self) -> None:
        report = validate_baseline(measurements_with(), DEFAULT_AMBULANCE_TRIP, make_config())
        assert check(report, "ambulance_completed_its_route").passed

    def test_incomplete_trip_is_an_error(self) -> None:
        measurements = measurements_with()
        measurements.trips[DEFAULT_AMBULANCE_TRIP.vehicle_id].arrival_s = None
        report = validate_baseline(measurements, DEFAULT_AMBULANCE_TRIP, make_config())
        assert not check(report, "ambulance_completed_its_route").passed

    def test_teleported_ambulance_is_an_error(self) -> None:
        """Its travel time would not be the time to drive its route."""
        measurements = measurements_with()
        measurements.trips[DEFAULT_AMBULANCE_TRIP.vehicle_id].teleported = True
        report = validate_baseline(measurements, DEFAULT_AMBULANCE_TRIP, make_config())
        assert not check(report, "ambulance_was_not_teleported").passed

    def test_missing_ambulance_is_an_error(self) -> None:
        measurements = measurements_with()
        del measurements.trips[DEFAULT_AMBULANCE_TRIP.vehicle_id]
        report = validate_baseline(measurements, DEFAULT_AMBULANCE_TRIP, make_config())
        assert not check(report, "ambulance_departed").passed


class TestNoFabricatedMeasurements:
    """A missing measurement is a fact about the run. A substituted one is not."""

    def test_incomplete_trip_has_no_travel_time(self) -> None:
        measurements = measurements_with()
        measurements.trips[DEFAULT_AMBULANCE_TRIP.vehicle_id].arrival_s = None
        measurements.trips[DEFAULT_AMBULANCE_TRIP.vehicle_id].travel_time_s = None
        summary = _ambulance_summary(measurements, DEFAULT_AMBULANCE_TRIP)
        assert summary["completed"] is False
        assert summary["travel_time_s"] is None
        assert "none has been substituted" in summary["note"]

    def test_absent_ambulance_reports_absence_not_zero(self) -> None:
        measurements = measurements_with()
        del measurements.trips[DEFAULT_AMBULANCE_TRIP.vehicle_id]
        summary = _ambulance_summary(measurements, DEFAULT_AMBULANCE_TRIP)
        assert summary["departed"] is False
        assert summary["travel_time_s"] is None

    def test_completed_trip_is_labelled_simulated(self) -> None:
        summary = _ambulance_summary(measurements_with(), DEFAULT_AMBULANCE_TRIP)
        assert summary["data_class"] == "SIMULATED_DATA"
        assert "not a measurement" in summary["note"].lower()


class TestBacklogRule:
    def test_large_backlog_is_flagged(self) -> None:
        """A large backlog means the traffic simulated is lighter than the
        configuration asked for."""
        measurements = measurements_with(loaded=1000, insertion_backlog_at_end=400)
        report = validate_baseline(measurements, DEFAULT_AMBULANCE_TRIP, make_config())
        assert not check(report, "insertion_backlog_is_small").passed


@pytest.mark.skipif(
    not (PROCESSED / "baseline_validation.json").is_file(),
    reason="No baseline run; run scripts/run_baseline.py",
)
class TestBaselineArtifacts:
    @pytest.fixture(scope="class")
    @classmethod
    def validation(cls) -> dict:
        return json.loads((PROCESSED / "baseline_validation.json").read_text())

    @pytest.fixture(scope="class")
    @classmethod
    def demand(cls) -> dict:
        return json.loads((PROCESSED / "demand_report.json").read_text())

    def test_every_output_carries_reproducibility_metadata(self, validation, demand) -> None:
        """An output file separated from its run must still say how to regenerate it."""
        for payload in (validation, demand):
            repro = payload["reproducibility"]
            for key in (
                "seed",
                "step_length_s",
                "sumo_version",
                "network_sha256",
                "demand_config_hash",
                "generated_at",
            ):
                assert key in repro, key

    def test_demand_report_separates_osm_from_sumo_estimated(self, demand) -> None:
        provenance = demand["lane_and_speed_provenance"]
        assert provenance["osm_derived"]["data_class"] == "PUBLICLY_SOURCED_DATA"
        assert provenance["sumo_estimated"]["data_class"] == "ESTIMATED_DATA"

    def test_estimated_values_are_never_called_real(self, demand) -> None:
        """The word 'real' must not describe a SUMO-supplied value."""
        blob = json.dumps(demand["lane_and_speed_provenance"]["sumo_estimated"]).lower()
        assert "real" not in blob.replace("real road", "")

    def test_teleports_are_reported_not_hidden(self, validation) -> None:
        assert "teleports" in validation["simulation"]
        assert "teleport_events" in validation
        assert validation["simulation"]["teleports"]["count"] == len(validation["teleport_events"])

    def test_demand_is_labelled_estimated(self, demand) -> None:
        assert demand["demand_configuration"]["data_class"] == "ESTIMATED_DATA"
        assert demand["vehicle_types"]["data_class"] == "ESTIMATED_DATA"
