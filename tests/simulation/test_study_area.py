"""Tests for the study-area definition.

The bounding box determines what ground every downstream measurement covers, so
these tests pin the properties that would otherwise fail silently: a box that is
not actually square in metres, and the two incompatible bbox orderings used by
the OSM API and Overpass.
"""

from __future__ import annotations

import pytest
from ems_sim.network.study_area import (
    SILK_BOARD,
    STUDY_AREAS,
    BoundingBox,
    get_study_area,
)


class TestBoundingBox:
    def test_rejects_inverted_bounds(self) -> None:
        with pytest.raises(ValueError, match="min_lon"):
            BoundingBox(min_lon=10.0, min_lat=0.0, max_lon=9.0, max_lat=1.0)
        with pytest.raises(ValueError, match="min_lat"):
            BoundingBox(min_lon=0.0, min_lat=10.0, max_lon=1.0, max_lat=9.0)

    def test_from_centre_is_square_in_metres(self) -> None:
        """A box built from a metre half-extent must be square on the ground.

        Applying the same degree offset to latitude and longitude would make the
        box ~2.5% narrower east-west at Bengaluru's latitude, which is the kind
        of error that never announces itself.
        """
        box = BoundingBox.from_centre(12.917236, 77.623262, 800.0)
        assert box.width_m == pytest.approx(1600, abs=5)
        assert box.height_m == pytest.approx(1600, abs=5)
        assert box.width_m == pytest.approx(box.height_m, rel=0.01)

    def test_from_centre_longitude_span_exceeds_latitude_span(self) -> None:
        """In degrees the box is wider than tall, because a degree of longitude
        is shorter than a degree of latitude away from the equator."""
        box = BoundingBox.from_centre(12.917236, 77.623262, 800.0)
        assert (box.max_lon - box.min_lon) > (box.max_lat - box.min_lat)

    def test_centre_round_trips(self) -> None:
        lat, lon = 12.917236, 77.623262
        box = BoundingBox.from_centre(lat, lon, 500.0)
        centre_lat, centre_lon = box.centre
        assert centre_lat == pytest.approx(lat, abs=1e-5)
        assert centre_lon == pytest.approx(lon, abs=1e-5)

    def test_rejects_non_positive_extent(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            BoundingBox.from_centre(12.9, 77.6, 0.0)

    def test_api_and_overpass_orderings_differ(self) -> None:
        """Overpass reverses the OSM API's bbox order.

        Swapping them yields a box somewhere else entirely rather than an error,
        so both orderings are asserted explicitly.
        """
        box = BoundingBox(min_lon=77.0, min_lat=12.0, max_lon=78.0, max_lat=13.0)
        assert box.as_api_bbox() == "77.0,12.0,78.0,13.0"  # W,S,E,N
        assert box.as_overpass_bbox() == "12.0,77.0,13.0,78.0"  # S,W,N,E

    def test_contains(self) -> None:
        box = BoundingBox(min_lon=77.0, min_lat=12.0, max_lon=78.0, max_lat=13.0)
        assert box.contains(12.5, 77.5)
        assert not box.contains(11.0, 77.5)
        assert not box.contains(12.5, 79.0)


class TestSilkBoardStudyArea:
    def test_anchor_is_inside_its_own_box(self) -> None:
        assert SILK_BOARD.bbox.contains(SILK_BOARD.anchor_lat, SILK_BOARD.anchor_lon)

    def test_anchor_derivation_is_recorded(self) -> None:
        """The anchor is a derived value, and the derivation is the evidence.

        Without it the coordinate is indistinguishable from one read off a map,
        which is exactly what this project must not do.
        """
        assert len(SILK_BOARD.anchor_derivation) > 100
        assert "centroid" in SILK_BOARD.anchor_derivation.lower()
        assert SILK_BOARD.anchor_source_ways, "anchor must name the ways it came from"
        assert all(w.startswith("way/") for w in SILK_BOARD.anchor_source_ways)

    def test_anchor_is_in_bengaluru(self) -> None:
        """Coarse guard against a transposed lat/lon, which would otherwise place
        the study area in the Indian Ocean and still look like valid floats."""
        assert 12.5 < SILK_BOARD.anchor_lat < 13.5
        assert 77.0 < SILK_BOARD.anchor_lon < 78.0

    def test_area_is_small_enough_to_run(self) -> None:
        assert SILK_BOARD.bbox.area_km2 < 10.0

    def test_scope_limits_are_documented(self) -> None:
        """The notes must carry the sizing rationale and the double-decker
        truncation, since both bound what the results can claim."""
        notes = SILK_BOARD.notes.lower()
        assert "double decker" in notes
        assert "5-10" in notes or "5–10" in notes

    def test_serialises_completely(self) -> None:
        payload = SILK_BOARD.as_dict()
        for key in ("area_id", "anchor", "anchor_derivation", "bbox", "half_extent_m"):
            assert key in payload

    def test_lookup(self) -> None:
        assert get_study_area("silk_board_v1") is SILK_BOARD
        with pytest.raises(KeyError, match="Unknown study area"):
            get_study_area("nowhere")

    def test_registry_keys_match_ids(self) -> None:
        for key, area in STUDY_AREAS.items():
            assert key == area.area_id
