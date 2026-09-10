"""Tests for OSM parsing and attribute preservation.

These run against a hand-written fixture (``fixtures/mini.osm.xml``) rather than
the real extract, so they stay deterministic as OSM changes underneath us. The
fixture sits near (0, 0) on purpose: it can never be mistaken for data about a
real place.

The tests are grouped by what would go wrong if the code regressed, because the
failure modes here are all silent. A flattened flyover, an invented lane count
and a resolved ``IN:urban`` speed limit all produce a network that builds, runs,
and reports plausible numbers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ems_sim.network.osm_parse import (
    DRIVABLE_HIGHWAY,
    PRESERVED_WAY_TAGS,
    metric_crs,
    normalise_oneway,
    parse_int_tag,
    parse_maxspeed,
    parse_osm_file,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mini.osm.xml"


@pytest.fixture(scope="module")
def network():
    return parse_osm_file(FIXTURE)


class TestTagParsers:
    def test_maxspeed_numeric(self) -> None:
        assert parse_maxspeed("60") == (60.0, "parsed_kmh")
        assert parse_maxspeed("60 km/h") == (60.0, "parsed_kmh")

    def test_maxspeed_mph_converted(self) -> None:
        value, status = parse_maxspeed("30 mph")
        assert value == pytest.approx(48.28, abs=0.01)
        assert status == "parsed_mph"

    def test_maxspeed_symbolic_is_not_invented(self) -> None:
        """``IN:urban`` must stay unresolved.

        Turning it into a number would assert a legal default speed for Indian
        urban roads that this project has not sourced from anywhere.
        """
        value, status = parse_maxspeed("IN:urban")
        assert value is None
        assert status == "unparsed:IN:urban"

    def test_maxspeed_absent_is_distinguishable_from_unparsed(self) -> None:
        """ "Not tagged" and "tagged but not numeric" are different facts."""
        assert parse_maxspeed(None) == (None, "absent")
        assert parse_maxspeed("walk")[1].startswith("unparsed")

    def test_int_tag_handles_osm_multivalues(self) -> None:
        assert parse_int_tag("3") == 3
        assert parse_int_tag("2;3") == 2
        assert parse_int_tag("garbage") is None
        assert parse_int_tag(None) is None

    def test_oneway_explicit_tags(self) -> None:
        assert normalise_oneway("yes", None, "primary") == (True, "tag:yes")
        assert normalise_oneway("no", None, "primary") == (False, "tag:no")
        assert normalise_oneway("-1", None, "primary")[0] is True

    def test_oneway_roundabout_is_flagged_as_implied(self) -> None:
        """Roundabouts are one-way by OSM convention, not by tag.

        The result is the same but the basis differs, and the basis is what a
        reviewer needs in order to judge it.
        """
        result, basis = normalise_oneway(None, "roundabout", "primary")
        assert result is True
        assert basis == "implied:roundabout"

    def test_oneway_default_records_its_assumption(self) -> None:
        result, basis = normalise_oneway(None, None, "residential")
        assert result is False
        assert "assumed" in basis


class TestParsedStructure:
    def test_counts(self, network) -> None:
        assert network.counts["osm_nodes_total"] == 9
        assert network.counts["drivable_edges"] == 9
        assert network.counts["turn_restrictions"] == 1

    def test_bounds_read_from_file(self, network) -> None:
        assert network.bounds == {
            "min_lat": 0.0,
            "min_lon": 0.0,
            "max_lat": 0.01,
            "max_lon": 0.01,
        }

    def test_non_drivable_ways_excluded(self, network) -> None:
        """A footway is not part of the road network."""
        assert "301" not in set(network.edges["osm_way_id"])
        assert all(h in DRIVABLE_HIGHWAY for h in network.edges["highway"])

    def test_geometry_is_wgs84(self, network) -> None:
        assert network.edges.crs.to_epsg() == 4326

    def test_lengths_computed_in_metres(self, network) -> None:
        assert (network.edges["length_m"] > 0).all()
        # Fixture spans ~0.004 deg latitude, so no edge can be kilometres long.
        assert network.edges["length_m"].max() < 2000


class TestAttributePreservation:
    """Every attribute Phase 1 is required to retain."""

    def test_all_preserved_tags_are_columns(self, network) -> None:
        for tag in PRESERVED_WAY_TAGS:
            assert tag in network.edges.columns, f"lost OSM tag {tag}"

    def test_required_phase1_attributes_present(self, network) -> None:
        required = {
            "geometry",  # road geometry + lat/lon
            "oneway",  # one-way status, raw
            "oneway_normalised",
            "lanes",
            "highway",  # road classification
            "bridge",
            "tunnel",
            "layer_raw",
            "name",
            "maxspeed",
            "is_link",  # ramps
            "service",  # service roads
        }
        assert required <= set(network.edges.columns)

    def test_raw_tags_survive_alongside_derived(self, network) -> None:
        """Derived columns must sit next to the originals, never replace them."""
        flyover = network.edges.set_index("osm_way_id").loc["200"]
        assert flyover["bridge"] == "yes"  # raw
        assert flyover["is_bridge"]  # derived
        assert flyover["layer"] == "1"  # raw, as a string, exactly as OSM had it
        assert flyover["layer_raw"] == 1  # derived

    def test_missing_lanes_stay_null(self, network) -> None:
        """A missing lane count must not become a default.

        Lane counts drive capacity, capacity drives queue length, and queue
        length is what this project reports.
        """
        untagged = network.edges[network.edges["lanes"].isna()]
        assert len(untagged) > 0
        assert untagged["lanes_parsed"].isna().all()

    def test_osm_versioning_retained(self, network) -> None:
        """Version and timestamp pin the extract to OSM's edit history."""
        assert network.edges["osm_version"].notna().all()


class TestElevatedStructures:
    """The Silk Board-critical behaviour: do not flatten the flyover."""

    def test_bridges_detected(self, network) -> None:
        bridges = network.edges[network.edges["is_bridge"]]
        assert len(bridges) == 2
        assert set(bridges["name"]) == {"Test Flyover", "Test Upper Deck"}

    def test_bridge_viaduct_counts_as_bridge(self, network) -> None:
        """``bridge=viaduct`` is a bridge; only ``bridge=no`` is not."""
        deck = network.edges.set_index("osm_way_id").loc["201"]
        assert deck["bridge"] == "viaduct"
        assert deck["is_bridge"]

    def test_distinct_grade_levels_preserved(self, network) -> None:
        """Ground, deck 1, deck 2 and a tunnel must remain four levels.

        If these collapse to one, traffic can change level for free and every
        travel time through the junction becomes wrong.
        """
        levels = set(network.edges["layer_effective"])
        assert {-1, 0, 1, 2} <= levels

    def test_ways_at_same_nodes_but_different_layers_stay_separate(self, network) -> None:
        """Ground trunk and flyover share nodes 1-2-3 yet must not be merged."""
        edges = network.edges
        ground = edges[edges["osm_way_id"] == "100"]
        elevated = edges[edges["osm_way_id"] == "200"]

        # Both survive as their own rows rather than being collapsed into one.
        assert len(ground) == 1
        assert len(elevated) == 1
        assert ground.iloc[0]["layer_effective"] == 0
        assert elevated.iloc[0]["layer_effective"] == 1

        # They genuinely overlap in plan view - which is exactly why only the
        # layer distinguishes them, and why losing it would flatten the junction.
        assert ground.iloc[0]["geometry"].intersects(elevated.iloc[0]["geometry"])

    def test_tunnel_detected_with_negative_layer(self, network) -> None:
        tunnel = network.edges.set_index("osm_way_id").loc["203"]
        assert tunnel["is_tunnel"]
        assert tunnel["layer_effective"] == -1

    def test_ramps_detected(self, network) -> None:
        links = network.edges[network.edges["is_link"]]
        assert len(links) == 1
        assert links.iloc[0]["highway"] == "trunk_link"


class TestNonCurrentInfrastructure:
    def test_construction_excluded_but_reported(self, network) -> None:
        """Under-construction ways are not drivable, but their existence is a
        finding: OSM cannot tell us whether they are open today."""
        assert "300" not in set(network.edges["osm_way_id"])
        assert network.counts["non_current_highway_ways"] == 1
        assert network.non_current_ways[0]["name"] == "Test Future Road"


class TestJunctionsAndIntersections:
    def test_junctions_require_three_ways(self, network) -> None:
        assert (network.junctions["degree"] >= 3).all()

    def test_major_flag_set(self, network) -> None:
        assert network.junctions["is_major"].any()

    def test_intersections_are_clustered_junctions(self, network) -> None:
        assert len(network.intersections) <= len(network.junctions)
        assert (network.intersections["node_count"] >= 1).all()

    def test_signal_association_is_labelled_as_derived(self, network) -> None:
        """OSM does not link a signal node to the junction it controls.

        The association is this project's inference and every row must say so,
        or a later reader will take it for an OSM fact.
        """
        assert network.counts["intersections_with_signal_nodes"] >= 1
        methods = set(network.intersections["association_method"])
        assert all("derived" in m and "not asserted by OSM" in m for m in methods)

    def test_intersections_returned_in_wgs84(self, network) -> None:
        assert network.intersections.crs.to_epsg() == 4326


class TestTurnRestrictions:
    def test_restriction_parsed_with_roles(self, network) -> None:
        restriction = network.turn_restrictions[0]
        assert restriction["restriction"] == "no_left_turn"
        assert restriction["from"] == ["100"]
        assert restriction["via"] == ["2"]
        assert restriction["to"] == ["102"]

    def test_restrictions_not_applied_to_geometry(self, network) -> None:
        """Restrictions are recorded, not baked in: SUMO enforces turns, and two
        sources of truth about legality would eventually disagree."""
        assert "restriction" not in network.edges.columns


class TestMetricCrs:
    def test_derived_from_data_not_hard_coded(self, network) -> None:
        """The fixture is near (0, 0), so its zone must not be Bengaluru's.

        This is the regression guard for a real bug: hard-coding UTM 43N made
        every distance in the fixture meaningless while still returning floats.
        """
        assert metric_crs(network.edges).to_epsg() != 32643
