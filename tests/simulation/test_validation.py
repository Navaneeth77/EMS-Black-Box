"""Tests for the validation layer.

The point of these is adversarial: each one constructs a network that is broken
in a specific way and asserts the suite notices. A validation suite that only
ever sees good input is untested, and every failure mode here is one that would
otherwise produce a network that builds, runs, and reports wrong numbers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ems_sim.network.osm_parse import parse_osm_file
from ems_sim.network.study_area import BoundingBox
from ems_sim.network.validation import Severity, validate_network

FIXTURE = Path(__file__).parent / "fixtures" / "mini.osm.xml"
FIXTURE_BBOX = BoundingBox(min_lon=0.0, min_lat=0.0, max_lon=0.01, max_lat=0.01)


@pytest.fixture(scope="module")
def network():
    return parse_osm_file(FIXTURE)


@pytest.fixture(scope="module")
def report(network):
    return validate_network(network, FIXTURE_BBOX)


def check(report, name):
    matches = [c for c in report.checks if c.name == name]
    assert matches, f"no check named {name!r}; have {[c.name for c in report.checks]}"
    return matches[0]


class TestReportStructure:
    def test_every_check_explains_itself(self, report) -> None:
        """A check that fails without saying why cannot be acted on."""
        for result in report.checks:
            assert result.detail, f"{result.name} has no detail"
            assert result.severity in set(Severity)

    def test_warnings_do_not_block(self, report) -> None:
        """OSM is community-mapped; missing attributes are expected.

        Only ERROR-severity failures make an extract unusable.
        """
        assert report.ok == (len(report.errors) == 0)

    def test_limitations_are_derived_from_warnings(self, report) -> None:
        assert len(report.limitations()) == len(report.warnings)

    def test_serialises(self, report) -> None:
        payload = report.as_dict()
        assert set(payload) == {"ok", "counts", "checks"}
        assert payload["counts"]["total"] == len(report.checks)


class TestStructuralChecks:
    def test_detects_elevated_structures(self, report) -> None:
        assert check(report, "elevated_structures_present").passed

    def test_detects_multiple_grade_levels(self, report) -> None:
        assert check(report, "multiple_grade_levels_present").passed

    def test_detects_ramps(self, report) -> None:
        assert check(report, "ramps_present").passed

    def test_geometry_valid(self, report) -> None:
        assert check(report, "edge_geometry_valid").passed

    def test_classification_complete(self, report) -> None:
        assert check(report, "attribute_coverage_highway").passed


class TestFlattenedNetworkIsRejected:
    """The single most important guard in the suite.

    A network whose flyover has been flattened to ground level converts cleanly,
    simulates cleanly, and is wrong. Nothing downstream can detect it, so it has
    to be caught here.
    """

    def test_removing_bridges_raises_an_error(self, network) -> None:
        flattened = network
        edges = flattened.edges.copy()
        edges["is_bridge"] = False
        flattened_network = type(network)(
            edges=edges,
            nodes=network.nodes,
            junctions=network.junctions,
            intersections=network.intersections,
            turn_restrictions=network.turn_restrictions,
            counts=network.counts,
            bounds=network.bounds,
        )
        report = validate_network(flattened_network, FIXTURE_BBOX)
        result = check(report, "elevated_structures_present")
        assert not result.passed
        assert result.severity is Severity.ERROR
        assert not report.ok

    def test_collapsing_layers_raises_an_error(self, network) -> None:
        """Bridges kept, but every layer forced to ground level."""
        edges = network.edges.copy()
        edges["layer_effective"] = 0
        collapsed = type(network)(
            edges=edges,
            nodes=network.nodes,
            junctions=network.junctions,
            intersections=network.intersections,
            turn_restrictions=network.turn_restrictions,
            counts=network.counts,
            bounds=network.bounds,
        )
        report = validate_network(collapsed, FIXTURE_BBOX)
        result = check(report, "multiple_grade_levels_present")
        assert not result.passed
        assert result.severity is Severity.ERROR


class TestTruncationIsDetected:
    def test_mismatched_bbox_is_an_error(self, network) -> None:
        """A clipped extract yields a network with missing approaches and
        travel times that are simply too short."""
        wrong = BoundingBox(min_lon=50.0, min_lat=5.0, max_lon=51.0, max_lat=6.0)
        report = validate_network(network, wrong)
        result = check(report, "served_bbox_matches_request")
        assert not result.passed
        assert result.severity is Severity.ERROR


class TestProvenanceRelevantChecks:
    def test_signal_timings_recorded_as_unavailable(self, report) -> None:
        """OSM has signal locations and no timings.

        Recording the absence matters: once a network has traffic-light nodes it
        becomes easy to treat whatever timings appear later as belonging to them.
        """
        result = check(report, "signal_timings_unavailable")
        assert result.passed
        assert "ESTIMATED_DATA" in result.detail

    def test_signal_association_declared_derived(self, report) -> None:
        result = check(report, "signal_association_is_derived")
        assert result.value["asserted_by_osm"] is False

    def test_non_current_infrastructure_flagged(self, report) -> None:
        result = check(report, "non_current_infrastructure_flagged")
        assert not result.passed  # the fixture contains one construction way
        assert result.severity is Severity.WARNING

    def test_junction_counts_reported_under_all_definitions(self, report) -> None:
        """ "How many intersections" has no single answer; all three are recorded
        so the study-area sizing cannot be quietly justified by whichever number
        happens to suit."""
        value = check(report, "junction_counts_recorded").value
        assert set(value) == {
            "junction_nodes_all",
            "junction_nodes_major",
            "intersections_clustered",
            "intersections_with_signal_nodes",
        }

    def test_metric_crs_mismatch_is_an_error(self, report) -> None:
        """The fixture is near (0, 0), so its UTM zone is not Bengaluru's.

        The suite must notice rather than reporting distorted metre values.
        """
        result = check(report, "metric_crs_is_utm_43n")
        assert not result.passed
        assert result.severity is Severity.ERROR
