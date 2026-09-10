"""Tests for route validation.

These run against the generated route file, because the properties being checked
are properties of real routes over the real network. Skipped when no demand has
been generated.

The framing throughout: a broken route does not stop SUMO. It produces a run that
completes and reports travel times that are not the times to drive those
journeys. That is what these checks exist to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ems_sim.demand.ambulance import DEFAULT_AMBULANCE_TRIP
from ems_sim.demand.config import (
    BOUNDARY_SINKS,
    BOUNDARY_SOURCES,
    DEFAULT_DEMAND_SCALE,
    make_config,
)
from ems_sim.demand.route_validation import (
    UNRESOLVED_OSM_WAYS,
    RouteFacts,
    collect_route_facts,
    validate_routes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NET_FILE = REPO_ROOT / "simulation" / "sumo" / "silk_board_v1" / "silk_board_v1.net.xml"
CONFIG = make_config(demand_scale=DEFAULT_DEMAND_SCALE)
ROUTES_FILE = REPO_ROOT / "simulation" / "routes" / f"{CONFIG.demand_id}.rou.xml"
GPKG = REPO_ROOT / "data" / "processed" / "silk_board_v1" / "network.gpkg"


def check(report, name):
    matches = [c for c in report.checks if c.name == name]
    assert matches, f"no check named {name!r}"
    return matches[0]


class TestValidationRules:
    """The rules, tested on constructed facts so they are fast and deterministic."""

    def test_broken_route_is_an_error(self) -> None:
        facts = RouteFacts(vehicle_count=1, ambulance_route=["a", "b"])
        facts.broken_routes = [{"vehicle": "v", "from": "a", "to": "b", "reason": "x"}]
        report = validate_routes(facts, BOUNDARY_SOURCES, BOUNDARY_SINKS)
        assert not check(report, "routes_are_connected").passed
        assert not report.ok

    def test_illegal_grade_transition_is_an_error(self) -> None:
        facts = RouteFacts(vehicle_count=1, ambulance_route=["a", "b"])
        facts.illegal_grade_transitions = [{"vehicle": "v", "from": "a", "to": "b"}]
        report = validate_routes(facts, BOUNDARY_SOURCES, BOUNDARY_SINKS)
        assert not check(report, "no_illegal_grade_transitions").passed

    def test_entry_outside_a_boundary_source_is_an_error(self) -> None:
        facts = RouteFacts(vehicle_count=1, ambulance_route=["a", "b"])
        facts.boundary_entry_counts = {"not_a_source": 3}
        report = validate_routes(facts, BOUNDARY_SOURCES, BOUNDARY_SINKS)
        assert not check(report, "traffic_enters_at_boundary_sources").passed

    def test_ambulance_without_a_route_is_an_error(self) -> None:
        report = validate_routes(RouteFacts(vehicle_count=1), BOUNDARY_SOURCES, BOUNDARY_SINKS)
        assert not check(report, "ambulance_has_a_route").passed

    def test_unresolved_infrastructure_is_counted_not_blocked(self) -> None:
        """Excluding way/1351994264 would isolate a 1.9 km carriageway, so it is
        left enabled — but every vehicle using it is counted, because their
        travel times are conditional on a fact nobody has verified."""
        facts = RouteFacts(vehicle_count=1, ambulance_route=["a", "b"])
        facts.routes_using_unresolved = [{"vehicle": "v", "osm_ways": ["1351994264"]}]
        report = validate_routes(
            facts, BOUNDARY_SOURCES, BOUNDARY_SINKS, allow_unresolved_infrastructure=True
        )
        result = check(report, "unresolved_infrastructure_usage_recorded")
        assert result.passed
        assert result.value["vehicles"] == 1

    def test_unresolved_ways_carry_over_from_phase_2_5(self) -> None:
        assert "1351994264" in UNRESOLVED_OSM_WAYS


@pytest.mark.skipif(
    not (ROUTES_FILE.is_file() and NET_FILE.is_file() and GPKG.is_file()),
    reason="No generated routes; run scripts/run_baseline.py --demand-only",
)
class TestGeneratedRoutes:
    @pytest.fixture(scope="class")
    @classmethod
    def facts(cls) -> RouteFacts:
        import geopandas as gpd

        edges = gpd.read_file(GPKG, layer="edges")
        layers = {
            str(w): int(layer)
            for w, layer in zip(edges["osm_way_id"], edges["layer_effective"], strict=True)
        }
        return collect_route_facts(
            ROUTES_FILE,
            NET_FILE,
            layers,
            CONFIG.begin_s,
            CONFIG.end_s,
            DEFAULT_AMBULANCE_TRIP.vehicle_id,
        )

    def test_routes_were_generated(self, facts) -> None:
        assert facts.vehicle_count > 100

    def test_no_route_is_broken(self, facts) -> None:
        assert facts.broken_routes == [], facts.broken_routes[:3]

    def test_no_route_crosses_a_grade_separation(self, facts) -> None:
        assert facts.illegal_grade_transitions == [], facts.illegal_grade_transitions[:3]

    def test_all_traffic_enters_and_leaves_at_the_boundary(self, facts) -> None:
        assert set(facts.boundary_entry_counts) <= set(BOUNDARY_SOURCES)
        assert set(facts.boundary_exit_counts) <= set(BOUNDARY_SINKS)

    def test_every_departure_is_inside_the_demand_window(self, facts) -> None:
        assert facts.departures_outside_window == []

    def test_the_ambulance_has_a_multi_edge_route(self, facts) -> None:
        assert len(facts.ambulance_route) > 5

    def test_ambulance_route_starts_and_ends_where_configured(self, facts) -> None:
        assert facts.ambulance_route[0] == DEFAULT_AMBULANCE_TRIP.origin_edge
        assert facts.ambulance_route[-1] == DEFAULT_AMBULANCE_TRIP.destination_edge

    def test_full_validation_passes(self, facts) -> None:
        report = validate_routes(facts, BOUNDARY_SOURCES, BOUNDARY_SINKS)
        assert report.ok, [f"{c.name}: {c.detail}" for c in report.errors]
