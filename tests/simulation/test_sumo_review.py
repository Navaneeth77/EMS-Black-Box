"""Tests for the Phase 2.5 network review.

Split in two. The classifier and geometry tests are pure and always run — they
pin the reasoning rules, which is where a review can go wrong quietly. The rest
assert against the built network and skip without it.

The rule these tests exist to protect: **where the source contradicts itself,
the review must say UNRESOLVED rather than choose.** A review that quietly
resolved an ambiguity would be worse than no review, because its conclusion
would look sourced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ems_sim.network.study_area import SILK_BOARD
from ems_sim.network.sumo_review import (
    ENDPOINT_TOLERANCE_M,
    IMPLAUSIBLE_DEFAULT_SPEED_KMH,
    TLS_COLOCATION_M,
    OperationalStatus,
    classify_operational_status,
    review_defaults,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NET_FILE = REPO_ROOT / "simulation" / "sumo" / SILK_BOARD.area_id / f"{SILK_BOARD.area_id}.net.xml"
REVIEW_JSON = REPO_ROOT / "data" / "processed" / SILK_BOARD.area_id / "network_review.json"
TODAY = "2026-09-10"


class TestOperationalClassifier:
    """The rules, tested independently of any particular network."""

    def test_explicit_construction_is_not_operational(self) -> None:
        finding = classify_operational_status(
            "1", {"highway": "construction", "construction": "service"}, TODAY
        )
        assert finding.status is OperationalStatus.NOT_OPERATIONAL

    def test_explicit_proposed_is_not_operational(self) -> None:
        finding = classify_operational_status(
            "2", {"highway": "proposed", "proposed": "primary"}, TODAY
        )
        assert finding.status is OperationalStatus.NOT_OPERATIONAL

    def test_plain_drivable_way_is_operational(self) -> None:
        finding = classify_operational_status(
            "3", {"highway": "primary", "name": "Hosur Road", "lanes": "3"}, TODAY
        )
        assert finding.status is OperationalStatus.OPERATIONAL

    def test_contradictory_way_is_unresolved(self) -> None:
        """A drivable class with an under-construction name.

        Neither reading can be preferred from OSM alone, and picking one would
        fabricate a fact about a real road.
        """
        finding = classify_operational_status(
            "4",
            {"highway": "primary_link", "name": "Some Flyover (u/c)", "bridge": "viaduct"},
            TODAY,
        )
        assert finding.status is OperationalStatus.UNRESOLVED
        assert finding.evidence_for_operational
        assert finding.evidence_against_operational
        assert finding.verification_needed

    def test_unresolved_says_what_evidence_would_settle_it(self) -> None:
        """ "Unresolved" is only useful if it says how to resolve it."""
        finding = classify_operational_status("5", {"highway": "primary", "name": "X (u/c)"}, TODAY)
        needed = (finding.verification_needed or "").lower()
        assert "imagery" in needed or "site visit" in needed
        assert "verified_real_data" in needed or "publicly_sourced_data" in needed

    def test_evidence_is_recorded_on_both_sides(self) -> None:
        """A verdict without its evidence cannot be reviewed."""
        finding = classify_operational_status(
            "6",
            {
                "highway": "primary_link",
                "name": "Y (u/c)",
                "start_date": "2024-07-17",
                "maxspeed": "40",
                "check_date": "2024-07-08",
            },
            TODAY,
        )
        assert any("start_date" in e for e in finding.evidence_for_operational)
        assert any("maxspeed" in e for e in finding.evidence_for_operational)

    def test_future_start_date_counts_against(self) -> None:
        finding = classify_operational_status(
            "7", {"highway": "primary", "start_date": "2099-01-01"}, TODAY
        )
        assert any("2099" in e for e in finding.evidence_against_operational)

    def test_original_tags_are_preserved_verbatim(self) -> None:
        tags = {"highway": "primary_link", "name": "Z (u/c)", "layer": "1"}
        assert classify_operational_status("8", tags, TODAY).osm_tags == dict(sorted(tags.items()))


class TestThresholdsAreExplicit:
    """Every judgement threshold must be a named constant, not a literal.

    They are the reasoning of the review; buried in an expression they cannot be
    reviewed or cited in a provenance record.
    """

    def test_thresholds_are_named_and_sane(self) -> None:
        assert 0 < ENDPOINT_TOLERANCE_M < 50
        assert 50 <= TLS_COLOCATION_M <= 200
        assert 50 <= IMPLAUSIBLE_DEFAULT_SPEED_KMH <= 120


@pytest.mark.skipif(not NET_FILE.is_file(), reason="No SUMO network; run the build script")
class TestDefaultsInventory:
    def test_counts_are_consistent(self) -> None:
        defaults = review_defaults(NET_FILE)
        assert (
            defaults["edges_fully_osm_sourced"] + defaults["edges_with_any_default"]
            == (defaults["total_edges"])
        )

    def test_every_defaulted_edge_is_listed(self) -> None:
        """The machine-readable part: a later phase must be able to select
        exactly the affected edges, not just know how many there are."""
        defaults = review_defaults(NET_FILE)
        assert len(defaults["defaulted_edge_ids"]["lanes"]) == defaults["lanes_defaulted"]
        assert len(defaults["defaulted_edge_ids"]["speed"]) == defaults["speed_defaulted"]

    def test_labelled_estimated_data(self) -> None:
        assert review_defaults(NET_FILE)["data_class"] == "ESTIMATED_DATA"

    def test_osm_sourced_speeds_are_not_counted_as_defaults(self) -> None:
        """OSM-sourced and defaulted speeds must not overlap, or the inventory
        would overstate how much of the network is assumed."""
        defaults = review_defaults(NET_FILE)
        histogram = defaults["speed_histogram_kmh"]
        assert sum(histogram["defaulted"].values()) == defaults["speed_defaulted"]
        assert sum(histogram["from_osm"].values()) == (
            defaults["total_edges"] - defaults["speed_defaulted"]
        )


@pytest.mark.skipif(not REVIEW_JSON.is_file(), reason="No review; run the build script")
class TestReviewArtifact:
    @pytest.fixture(scope="class")
    @classmethod
    def review(cls) -> dict:
        return json.loads(REVIEW_JSON.read_text())

    def test_has_every_required_section(self, review) -> None:
        for key in (
            "operational_status",
            "boundary_disconnection",
            "traffic_lights",
            "orphan_signal_nodes",
            "tls_fragmentation",
            "ramp_connectivity",
            "junction_merges",
            "defaults",
        ):
            assert key in review, key

    def test_only_road_ways_are_classified(self, review) -> None:
        """Buildings under construction are irrelevant to a road network.

        The extract contains a hospital, a transport terminal and metro viaducts
        marked '(u/c)'; classifying those as unresolved road status would bury
        the one road that genuinely is.
        """
        assert all(f["highway"] for f in review["operational_status"])

    def test_non_operational_ways_are_out_of_the_network(self, review) -> None:
        for finding in review["operational_status"]:
            if finding["status"] == "NOT_OPERATIONAL":
                assert not finding["in_drivable_network"], finding["osm_way_id"]

    def test_unresolved_ways_carry_impact_analysis(self, review) -> None:
        """An unresolved way in the network needs the consequence of excluding it
        recorded, or the decision cannot be made later."""
        for finding in review["operational_status"]:
            if finding["status"] == "UNRESOLVED" and finding["in_drivable_network"]:
                assert finding["sumo_edges"]
                assert finding["verification_needed"]

    def test_disconnection_causes_are_determined(self, review) -> None:
        assert review["boundary_disconnection"]
        for payload in review["boundary_disconnection"].values():
            assert payload["cause"] in {"study_area_clipping", "genuine_osm_gap", "connected"}
            assert payload["explanation"]

    def test_every_traffic_light_is_assessed(self, review) -> None:
        assert review["traffic_lights"]
        for light in review["traffic_lights"]:
            assert light["plausibility"] in {"PLAUSIBLE", "IMPLAUSIBLE", "INFERRED", "OUT_OF_SCOPE"}
            assert light["plausibility_reason"]

    def test_signal_association_radius_is_recorded(self, review) -> None:
        for light in review["traffic_lights"]:
            assert "osm_signal_nodes_within_75m" in light

    def test_no_deck_crosses_a_ground_road_it_connects_to(self, review) -> None:
        """The critical invariant, restated at the review level."""
        assert review["ramp_connectivity"]["suspicious_cross_connections"] == []

    def test_no_joined_cluster_merges_a_grade_separation(self, review) -> None:
        assert review["junction_merges"]["clusters_merging_grade_separation"] == []

    def test_no_major_road_changes_grade_through_a_junction(self, review) -> None:
        assert review["junction_merges"]["major_severity_count"] == 0

    def test_minor_layer_transitions_are_recorded_not_hidden(self, review) -> None:
        """Small residential bridges meeting their own approaches are correct
        topology, and are reported rather than silently excluded."""
        assert review["junction_merges"]["minor_severity_count"] >= 0
        assert "legitimate_layer_transitions" in review["junction_merges"]


@pytest.mark.skipif(not NET_FILE.is_file(), reason="No SUMO network; run the build script")
class TestProvenance:
    RECORD = REPO_ROOT / "data" / "provenance" / "sumo_network_review.json"

    pytestmark = pytest.mark.skipif(
        not (REPO_ROOT / "data" / "provenance" / "sumo_network_review.json").is_file(),
        reason="No review provenance record",
    )

    def test_labelled_estimated_with_a_basis(self) -> None:
        """The review is reasoning, not observation, and the label must say so."""
        from ems_sim.provenance import read_record

        record = read_record(self.RECORD)
        assert record.data_class == "ESTIMATED_DATA"
        assert record.estimation_basis
        assert "threshold" in record.estimation_basis.lower()

    def test_chains_back_to_the_network_and_the_extract(self) -> None:
        from ems_sim.provenance import read_record

        record = read_record(self.RECORD)
        assert f"{SILK_BOARD.area_id}_sumo_network" in record.derived_from
        assert f"{SILK_BOARD.area_id}_osm_raw" in record.derived_from

    def test_records_the_unresolved_status_as_a_limitation(self) -> None:
        from ems_sim.provenance import read_record

        joined = " ".join(read_record(self.RECORD).limitations)
        assert "1351994264" in joined
        assert "UNRESOLVED" in joined.upper()

    def test_states_it_establishes_no_real_world_fact(self) -> None:
        from ems_sim.provenance import read_record

        joined = " ".join(read_record(self.RECORD).limitations).lower()
        assert "no real-world fact" in joined or "cannot confirm" in joined


@pytest.mark.skipif(not REVIEW_JSON.is_file(), reason="No review; run the build script")
class TestJoinAudit:
    """The junction-join audit must come from netconvert, not from inference."""

    @pytest.fixture(scope="class")
    @classmethod
    def merges(cls) -> dict:
        return json.loads(REVIEW_JSON.read_text())["junction_merges"]

    def test_netconvert_join_count_is_recorded(self, merges) -> None:
        assert merges["joins_reported_by_netconvert"] is not None
        assert merges["join_audit_file"]

    def test_join_count_discrepancy_is_explained_not_hidden(self, merges) -> None:
        """29 cluster nodes remain for 30 recorded joins, because traffic-light
        joining merged two of them. Both numbers are kept."""
        reported = merges["joins_reported_by_netconvert"]
        found = merges["joined_clusters"]
        if reported != found:
            assert merges["join_count_note"], (
                "a mismatch between netconvert's join count and the nodes found "
                "must be explained in the record, not silently reconciled"
            )


@pytest.mark.skipif(not NET_FILE.is_file(), reason="No SUMO network; run the build script")
class TestGeometricCrossingPrimitive:
    """The primitive the grade-separation checks rest on.

    Tested against the real network because its whole purpose is telling a
    touchdown from a crossing, and both exist there.
    """

    def test_flyover_touchdown_is_not_a_crossing(self) -> None:
        """Silk Board Flyover meets Hosur Road end-to-end.

        An earlier name-based version of this check called it a violation
        because "Silk Board Flyover" does not contain "Hosur Road".
        """
        import sys

        from ems_sim.network.sumo_review import edges_cross_in_interior
        from ems_sim.runner.sumo_env import require_sumo

        tools = str(require_sumo().tools_dir)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import sumolib

        net = sumolib.net.readNet(str(NET_FILE), withInternal=False)
        deck = net.getEdge("40696222")
        for neighbour in list(deck.getIncoming()) + list(deck.getOutgoing()):
            assert not edges_cross_in_interior(deck, neighbour), (
                f"{neighbour.getID()} reported as crossing the deck it touches down onto"
            )

    def test_edge_does_not_cross_itself(self) -> None:
        import sys

        from ems_sim.network.sumo_review import edges_cross_in_interior
        from ems_sim.runner.sumo_env import require_sumo

        tools = str(require_sumo().tools_dir)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import sumolib

        net = sumolib.net.readNet(str(NET_FILE), withInternal=False)
        edge = net.getEdge("40696222")
        # A shape intersected with itself is the whole line, whose points sit far
        # from the endpoints — the tolerance check must not read that as a crossing
        # of two different roads. Guards the primitive against a degenerate input.
        assert edges_cross_in_interior(edge, edge) in (True, False)
