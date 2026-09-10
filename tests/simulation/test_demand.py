"""Tests for demand configuration, generation and route validation.

Determinism gets the most attention here. A demand set that is not reproducible
makes the whole counterfactual design unusable: Phase 5 compares two runs that
must differ only in signal policy, and that is only checkable if the same
configuration provably produces the same traffic.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP, AmbulanceTripConfig
from ems_sim.demand.config import (
    BOUNDARY_SINKS,
    BOUNDARY_SOURCES,
    DEFAULT_SEED,
    PERIOD_SCALING,
    VEHICLE_MIX,
    DemandPeriod,
    make_config,
)
from ems_sim.demand.generator import write_flows
from ems_sim.provenance import DataClass

REPO_ROOT = Path(__file__).resolve().parents[2]
NET_FILE = REPO_ROOT / "simulation" / "sumo" / "silk_board_v1" / "silk_board_v1.net.xml"


class TestDemandConfig:
    def test_defaults_to_weekday_evening_peak(self) -> None:
        assert make_config().period is DemandPeriod.EVENING_PEAK

    def test_all_three_periods_are_supported(self) -> None:
        for period in DemandPeriod:
            assert make_config(period=period).total_vehicles_per_hour > 0

    def test_off_peak_is_lighter_than_both_peaks(self) -> None:
        assert PERIOD_SCALING[DemandPeriod.OFF_PEAK] < PERIOD_SCALING[DemandPeriod.MORNING_PEAK]
        assert PERIOD_SCALING[DemandPeriod.OFF_PEAK] < PERIOD_SCALING[DemandPeriod.EVENING_PEAK]

    def test_vehicle_mix_sums_to_one(self) -> None:
        assert sum(VEHICLE_MIX.values()) == pytest.approx(1.0, abs=1e-9)

    def test_mix_covers_only_background_types(self) -> None:
        """The ambulance is a single scheduled trip, not a share of background
        traffic. Including it in the mix would insert a stream of ambulances."""
        assert "ambulance" not in VEHICLE_MIX

    def test_seed_is_recorded_not_random(self) -> None:
        assert make_config().seed == DEFAULT_SEED
        assert make_config(seed=7).seed == 7

    def test_warmup_precedes_the_measured_window(self) -> None:
        """The ambulance must enter a populated network, not a filling one."""
        config = make_config()
        assert config.warmup_s > 0
        assert DEFAULT_AMBULANCE_TRIP.depart_time_s > config.warmup_s

    def test_config_hash_is_stable(self) -> None:
        assert make_config().config_hash() == make_config().config_hash()

    def test_config_hash_changes_with_seed_and_period(self) -> None:
        base = make_config().config_hash()
        assert make_config(seed=1).config_hash() != base
        assert make_config(period=DemandPeriod.OFF_PEAK).config_hash() != base

    def test_declares_itself_estimated(self) -> None:
        payload = make_config().as_dict()
        assert payload["data_class"] == str(DataClass.ESTIMATED)
        assert "vehicle_mix_basis" in payload
        assert "not" in payload["vehicle_mix_basis"].lower()

    def test_flows_run_between_documented_boundary_terminals(self) -> None:
        """Traffic must enter and leave where the clipped network allows it."""
        for flow in make_config().flows:
            assert flow.from_edge in BOUNDARY_SOURCES, flow.from_edge
            assert flow.to_edge in BOUNDARY_SINKS, flow.to_edge

    def test_period_scaling_is_applied(self) -> None:
        peak = make_config(period=DemandPeriod.EVENING_PEAK).total_vehicles_per_hour
        off = make_config(period=DemandPeriod.OFF_PEAK).total_vehicles_per_hour
        assert off == pytest.approx(peak * PERIOD_SCALING[DemandPeriod.OFF_PEAK], rel=1e-6)


class TestAmbulanceTrip:
    def test_vehicle_id_is_reproducible(self) -> None:
        assert AmbulanceTripConfig("a", "b", 1.0).vehicle_id == "ambulance_baseline"

    def test_default_trip_runs_between_boundary_terminals(self) -> None:
        assert DEFAULT_AMBULANCE_TRIP.origin_edge in BOUNDARY_SOURCES
        assert DEFAULT_AMBULANCE_TRIP.destination_edge in BOUNDARY_SINKS

    def test_declares_it_is_not_a_real_dispatch(self) -> None:
        payload = DEFAULT_AMBULANCE_TRIP.as_dict()
        assert payload["data_class"] == str(DataClass.ESTIMATED)
        assert "not a real EMS dispatch" in payload["basis"]

    def test_declares_no_priority_in_this_phase(self) -> None:
        assert DEFAULT_AMBULANCE_TRIP.as_dict()["priority_in_this_phase"].startswith("none")


class TestFlowFileGeneration:
    """Determinism, checked by hashing the generated file."""

    def test_same_config_produces_identical_bytes(self, tmp_path) -> None:
        config = make_config()
        first = write_flows(config, DEFAULT_AMBULANCE_TRIP, tmp_path / "a.xml")
        second = write_flows(config, DEFAULT_AMBULANCE_TRIP, tmp_path / "b.xml")
        assert hashlib.sha256(first.read_bytes()).hexdigest() == (
            hashlib.sha256(second.read_bytes()).hexdigest()
        )

    def test_different_seed_is_recorded_in_the_file(self, tmp_path) -> None:
        a = write_flows(make_config(seed=1), DEFAULT_AMBULANCE_TRIP, tmp_path / "a.xml")
        b = write_flows(make_config(seed=2), DEFAULT_AMBULANCE_TRIP, tmp_path / "b.xml")
        assert a.read_text() != b.read_text()
        assert "seed 1" in a.read_text()

    def test_is_valid_xml_with_types_flows_and_the_ambulance(self, tmp_path) -> None:
        path = write_flows(make_config(), DEFAULT_AMBULANCE_TRIP, tmp_path / "f.xml")
        root = ET.parse(path).getroot()
        assert len(root.findall("vType")) == 7
        assert len(root.findall("flow")) > 0
        trips = root.findall("trip")
        assert len(trips) == 1
        assert trips[0].get("id") == DEFAULT_AMBULANCE_TRIP.vehicle_id
        assert trips[0].get("type") == "ambulance"

    def test_flow_rates_follow_the_configured_mix(self, tmp_path) -> None:
        config = make_config()
        path = write_flows(config, DEFAULT_AMBULANCE_TRIP, tmp_path / "f.xml")
        root = ET.parse(path).getroot()
        by_type: dict[str, float] = {}
        for flow in root.findall("flow"):
            by_type[flow.get("type")] = by_type.get(flow.get("type"), 0.0) + float(
                flow.get("vehsPerHour")
            )
        total = sum(by_type.values())
        for type_id, share in config.vehicle_mix.items():
            assert by_type[type_id] / total == pytest.approx(share, abs=1e-6)

    def test_file_carries_its_provenance_labels(self, tmp_path) -> None:
        """A demand file separated from its report must still say what it is."""
        text = write_flows(make_config(), DEFAULT_AMBULANCE_TRIP, tmp_path / "f.xml").read_text()
        assert "ESTIMATED_DATA" in text
        assert "SIMULATED_DATA" in text
        assert "not measured Bengaluru traffic" in text

    def test_ambulance_flow_is_a_single_trip(self, tmp_path) -> None:
        """One ambulance, not a stream: the study is about one journey."""
        root = ET.parse(
            write_flows(make_config(), DEFAULT_AMBULANCE_TRIP, tmp_path / "f.xml")
        ).getroot()
        assert all(f.get("type") != "ambulance" for f in root.findall("flow"))


@pytest.mark.skipif(not NET_FILE.is_file(), reason="No SUMO network; run the build script")
class TestBoundaryTerminalsExist:
    """The configured boundary edges must be what the network actually offers."""

    @pytest.fixture(scope="class")
    @classmethod
    def net(cls):
        import sys

        from ems_sim.runner.sumo_env import require_sumo

        tools = str(require_sumo().tools_dir)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import sumolib

        return sumolib.net.readNet(str(NET_FILE), withInternal=False)

    def test_every_source_exists_and_accepts_traffic(self, net) -> None:
        for edge_id in BOUNDARY_SOURCES:
            edge = net.getEdge(edge_id)
            assert not edge.getIncoming(), (
                f"{edge_id} has incoming edges, so it is not a boundary source"
            )

    def test_every_sink_exists_and_releases_traffic(self, net) -> None:
        for edge_id in BOUNDARY_SINKS:
            edge = net.getEdge(edge_id)
            assert not edge.getOutgoing(), (
                f"{edge_id} has outgoing edges, so it is not a boundary sink"
            )

    def test_phase2_5_terminals_are_all_covered(self, net) -> None:
        """The four flyover terminals Phase 2.5 identified must be accounted for."""
        for edge_id in ("886153772", "684917326#0"):
            assert edge_id in BOUNDARY_SOURCES
        for edge_id in ("886153773", "172853384#3"):
            assert edge_id in BOUNDARY_SINKS
